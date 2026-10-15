"""报告实例、审核记录与推送记录。

实例来源两种：
- 任务触发（task_id 有值、source=cron/manual）
- 报告页手动创建（task_id 为 null、source=manual），仍走"生成 -> 确认 -> 推送"
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONVariant, TimestampMixin

# 7 态：pending / generating / pending_confirmation / confirmed /
#      pushed / push_failed / failed
REPORT_STATUSES = (
    "pending",
    "generating",
    "pending_confirmation",
    "confirmed",
    "pushed",
    "push_failed",
    "failed",
)

# 实例来源
REPORT_SOURCES = ("cron", "manual")

# error_stage 取值
ERROR_STAGES = ("generate", "push", "confirm")


class Report(TimestampMixin, Base):
    __tablename__ = "reports"
    __table_args__ = (
        # 同一任务的同一次触发只能产生一个实例（幂等的正确性保证，
        # 手动触发的 scheduled_for 带微秒，天然不冲突；task_id 为空不受约束）
        Index(
            "uq_report_task_slot",
            "task_id",
            "scheduled_for",
            unique=True,
            postgresql_where=text("task_id IS NOT NULL"),
            sqlite_where=text("task_id IS NOT NULL"),
        ),
        # 提醒扫描：status + expires_at
        Index("ix_report_pending_confirm", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # 任务触发时有关联；手动实例为 null
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("report_tasks.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    template_id: Mapped[int] = mapped_column(
        ForeignKey("word_templates.id"), nullable=False
    )
    # 生成时使用的模板版本（历史报告可追溯到当时的模板）
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("template_versions.id"), nullable=True, index=True
    )
    # 本次执行对应的 cron 触发时刻（幂等键的一部分）
    scheduled_for: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # cron | manual
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)

    # 统计周期，例如 "2026-09"
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    # 事实层快照：[MetricFact 序列化 dict]，含 source/object/formula/period 证据字段。
    # 指标值仅由计算引擎写入，AI 不可修改；快照后 metric 配置变化不影响历史报告
    facts_json: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)
    # 洞察层：{槽位key: {"text": ..., "unverified_numbers": [...], "degraded": bool}}
    insights_json: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # ---- 确认环节 ----
    # 一次性确认 token 的 **sha256 哈希**（明文只出现在邮件里，库里不落明文）
    confirmation_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmation_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 链接过期时刻 = confirmation_sent_at + TTL；过期后停提醒但状态不变
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 最近一次 24h 提醒的发送时刻（提醒计时基准）
    last_reminder_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 确认人邮箱快照（任务被改后历史实例仍可追溯当时发给谁确认）
    confirmer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 收件人快照（生成时指定）
    recipient_emails: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)
    pushed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 最近一次失败信息（生成/推送共用），成功时清空
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 触发者；定时任务触发的实例为 null（系统行为无用户）
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )


class ReportAudit(TimestampMixin, Base):
    __tablename__ = "report_audits"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    auditor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    # create / generate / generate_ok / generate_fail / confirm /
    # push / retry_push / resend_confirmation / regenerate / remind / skip
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class PushRecord(Base):
    """推送记录：报告邮件的**每次发送尝试**（成功与失败都记）。

    与 ReportAudit 的分工：audit 记状态机动作（谁点了推送），push_records 记
    发送事实（发给谁、几个、耗时、成功还是失败、失败原因）。
    """

    __tablename__ = "push_records"
    __table_args__ = (Index("ix_push_record_report", "report_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 该实例的第几次发送（从 1 开始，失败重试会递增）
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # push / retry_push
    action: Mapped[str] = mapped_column(String(16), default="push", nullable=False)
    # success / failed
    status: Mapped[str] = mapped_column(String(16), default="success", nullable=False)
    # 实际收件人快照
    recipients: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)
    recipient_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 附件文件名（发送时的报告文件名，便于核对发了哪个文件）
    attachment_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
