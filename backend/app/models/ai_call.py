"""AI 调用留痕：每一次「请求 AI」的完整参数、prompt 与响应。

与相邻表的分工：
- `ReportAudit`  记状态机动作（谁点了重生成 / 谁点了推送）
- `PushRecord`   记邮件发送事实（发给谁、成功还是失败）
- `AiCallLog`    记**请求事实**：真正送出去的 messages（system 红线 + user 段、
                 含指标 JSON）、模型参数、响应原文、护栏重试的第二次请求

覆盖 AI 的全部三个入口：报告生成（generate）/ 单段重生成（regenerate）/
模板试跑预览（preview）。试跑不落报告，但同样留痕，便于回溯「当初这段配了什么」。

红线（见 AGENTS.md）：**只写不读密钥** —— 本表不存 api_key，`base_url` 落库前
去 userinfo，`error` 文本统一脱敏，绝不出现 Bearer / Authorization / sk-xxx。
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONVariant

# 调用场景：报告生成 / 单段重生成 / 模板试跑
AI_CALL_KINDS = ("generate", "regenerate", "preview")

# 结果：success 正常出稿；degraded 降级为占位文本（未配 key / 网络失败等）；failed 其它失败
AI_CALL_STATUSES = ("success", "degraded", "failed")


class AiCallLog(Base):
    __tablename__ = "ai_call_logs"
    __table_args__ = (
        # 「某份报告的各段请求」与「某个模板的试跑历史」是两个主要查询方向
        Index("ix_ai_call_report", "report_id", "id"),
        Index("ix_ai_call_template", "template_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # 报告生成 / 单段重生成时有关联；模板试跑为 null
    report_id: Mapped[int | None] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), nullable=True, index=True
    )
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("word_templates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # generate | regenerate | preview
    kind: Mapped[str] = mapped_column(String(16), default="generate", nullable=False)
    # 摘要槽位（AI 占位符名 / 绑定 key）
    slot: Mapped[str | None] = mapped_column(String(128), nullable=True)
    period: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 触发者；定时任务触发的报告生成为 null（系统行为无用户）
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    # ---- 请求参数（密钥一律不落库）----
    model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    # 已去 userinfo 的 base_url（不含 api_key）
    base_url: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 是否带自定义提示词 / 范文（快速筛选，不必展开 messages）
    has_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 真正发出去的 messages（system + user，user 段含指标 JSON）
    messages: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)

    # ---- 响应 ----
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # success | degraded | failed
    status: Mapped[str] = mapped_column(String(16), default="success", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- 护栏重试：命中「照抄范文数字 / 整句复述」后的第二次请求 ----
    retried: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retry_messages: Mapped[list[Any] | None] = mapped_column(
        JSONVariant, nullable=True
    )
    retry_response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 首次请求耗时（毫秒，含重试则记两次总和）
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )