"""数据源配置。

非敏感配置存 config_json，敏感信息（API token）Fernet 加密后存
secret_encrypted，secret_hint 存脱敏提示（****末4位）供列表/详情直接返回，
读路径完全不接触明文。
"""

from typing import Any

from sqlalchemy import Boolean, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONVariant, TimestampMixin


class DataSource(TimestampMixin, Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        # 软删除友好的部分唯一索引：仅活跃记录之间 name 唯一
        Index(
            "uq_ds_name_active",
            "name",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    # zabbix / aiops（aiops 仅预留 Adapter 扩展点）
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    # 仅非敏感配置：{"url": "...", "auth_type": "bearer", "timeout": 30, "verify_ssl": true}
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    # Fernet.encrypt() 完整输出（Base64 token 字符串），非裸密文
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 脱敏提示，如 "****a1b2"；GET 响应直接返回本字段，无需解密
    secret_hint: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
