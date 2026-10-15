"""Word 模板、模板版本、占位符绑定与授权。

- WordTemplate 为「逻辑模板」：名称、属主、是否启用，并保留**当前版本**的
  file_path / placeholders_json 快照（渲染与列表快速访问）。
- TemplateVersion 记录每个历史版本的文件与占位符，支持回滚。
- TemplateBinding 归属到具体 version（按版本隔离，新版本复制旧版本再清理）。
- TemplateGrant 记录模板对用户的授权（view / edit）。
"""

from typing import Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONVariant, TimestampMixin

# 模板授权档位：view=只读；edit=可编辑文档+绑定
GRANT_PERMISSIONS = ("view", "edit")


class WordTemplate(TimestampMixin, Base):
    __tablename__ = "word_templates"
    __table_args__ = (
        Index(
            "uq_tpl_name_active",
            "name",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    # 属主（上传者）；历史数据可能为空，由迁移回填
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    # 当前版本 id（不建 FK，避免与 template_versions 循环依赖）
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 当前版本的快照（渲染/列表用；随版本切换更新）
    file_path: Mapped[str] = mapped_column(String(512), default="")
    placeholders_json: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TemplateVersion(TimestampMixin, Base):
    """模板的一个版本（上传=版本1；在线编辑保存=版本N+1）。"""

    __tablename__ = "template_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "version_no", name="uq_version_tpl_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("word_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(String(512))
    placeholders_json: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    # upload / edit
    source: Mapped[str] = mapped_column(String(16), default="upload", nullable=False)


class TemplateBinding(TimestampMixin, Base):
    """占位符绑定（按版本隔离）。

    binding_type = metric  -> binding_ref_id 指向 MetricDefinition.id
    binding_type = insight -> AI 摘要槽位；binding_ref_id 为空，
                              binding_key 标识槽位（缺省取 placeholder 名，
                              故「一个 AI 占位符 = 一段独立摘要」），
                              ai_config_json 存该槽位的 AI 写作配置
    """

    __tablename__ = "template_bindings"
    __table_args__ = (
        UniqueConstraint("version_id", "placeholder", name="uq_binding_ver_ph"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("template_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    placeholder: Mapped[str] = mapped_column(String(128))
    binding_type: Mapped[str] = mapped_column(String(16))  # metric / insight
    binding_ref_id: Mapped[int | None] = mapped_column(nullable=True)
    binding_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # insight 槽位的 AI 写作配置（metric 恒为 None）。全部可选，键随需求增补，
    # **新增键时同步更新这条注释**（真正的解析在
    # services/ai_insight.py::InsightConfig.from_mapping）：
    #   prompt / sample_text      —— 进 user message 的写作要求与参考范文
    #   max_chars / temperature / threshold_pct
    #   system_prompt / system_rules —— 本段专属的两段 system 规则文本（覆盖全局，
    #   留空 = 用全局，全局也为空 = 用内置常量）
    # 明文存储：提示词/范文/规则文本都不是密钥；模型 api_key 仍在 app_settings（Fernet）。
    # NULL 表示沿用默认（固定指令、200 字、无范文），与升级前行为一致。
    ai_config_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONVariant, nullable=True, default=None
    )

    @property
    def ai_config(self) -> dict[str, Any] | None:
        """对外字段名（库列名为 ai_config_json），供 schema 直接序列化。"""
        return self.ai_config_json


class TemplateGrant(TimestampMixin, Base):
    """模板对用户的授权（属主与 admin 之外的可访问者）。"""

    __tablename__ = "template_grants"
    __table_args__ = (
        UniqueConstraint("template_id", "user_id", name="uq_grant_tpl_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("word_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    permission: Mapped[str] = mapped_column(String(8), nullable=False)  # view / edit
