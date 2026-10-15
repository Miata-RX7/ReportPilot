"""报告任务（周期性生成 + 确认 + 推送）。

任务是"配置"：什么时候生成、给谁、谁确认。执行结果落在 Report（实例）。
DB 是唯一真相源，进程内 APScheduler 只是执行器。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONVariant, TimestampMixin


class ReportTask(TimestampMixin, Base):
    __tablename__ = "report_tasks"
    __table_args__ = (
        # 软删除友好的部分唯一索引：仅启用中的任务之间名字唯一
        Index(
            "uq_task_name_active",
            "name",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    # 绑定模板（必须是启用中的模板；模板删除时任务会被拒绝）
    template_id: Mapped[int] = mapped_column(
        ForeignKey("word_templates.id"), nullable=False
    )
    # 5 段式 cron：分 时 日 月 周，如 "0 9 1 * *" = 每月 1 号 09:00
    # 求值时区用 settings.report_timezone（容器是 UTC，不设会导致时刻错位）
    cron_expr: Mapped[str] = mapped_column(String(64), nullable=False)
    # 统计周期粒度：month=月报（默认，与升级前一致）/ week=周报
    period_granularity: Mapped[str] = mapped_column(
        String(8), default="month", nullable=False
    )
    # 周期偏移：0=触发时刻所在周期（默认）/ -1=上一个周期
    # （周一触发取上周、月初触发取上月）；由 period_utils.shift_period 派生
    period_offset: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 确认人：单个邮箱（必填）。既可以是通讯录里的用户，也可以是任意外部邮箱
    confirmer_email: Mapped[str] = mapped_column(String(255), nullable=False)
    # 最终收件人：至少 1 个
    recipient_emails: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)

    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # 迁移里已建该列，这里补上映射以便列表展示"最后修改时间"
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    # ---- 执行 bookkeeping（仅供列表展示与排查，不参与业务判定）----
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 下次执行时间：调度器注册/重排后回写，避免前端自己算 cron
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 最近一次"因停机/超出宽限期而跳过"的时刻（与 last_error 语义分开）
    last_skipped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 最近一次失败的说明（如 cron 解析失败、模板被停用导致跳过）
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
