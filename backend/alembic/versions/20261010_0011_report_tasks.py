"""报告任务 + 实例改造

- 新增 report_tasks 表
- reports 增加任务关联、确认环节字段、source/scheduled_for
- reports.created_by 改可空（定时触发的实例没有用户）
- 唯一索引 uq_report_task_slot（task_id + scheduled_for）保证同一次触发只生成一个实例
- 清空既有报告数据（评审决定：不考虑历史兼容）
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type() -> sa.types.TypeEngine:
    """与模型一致的跨库 JSON 类型（PG=JSONB，其它=JSON）。"""
    return sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    # ---------------- report_tasks ----------------
    op.create_table(
        "report_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("cron_expr", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("confirmer_email", sa.String(255), nullable=False),
        sa.Column(
            "recipient_emails",
            _json_type(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_skipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["template_id"], ["word_templates.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
    op.create_index(
        "uq_task_name_active",
        "report_tasks",
        ["name"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
        sqlite_where=sa.text("is_active = 1"),
    )

    # ---------------- reports 变更 ----------------
    op.add_column("reports", sa.Column("task_id", sa.Integer(), nullable=True))
    op.add_column(
        "reports", sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "reports",
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
    )
    op.add_column(
        "reports", sa.Column("confirmation_token", sa.String(64), nullable=True)
    )
    op.add_column(
        "reports",
        sa.Column("confirmation_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "reports", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "reports", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "reports",
        sa.Column("last_reminder_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "reports", sa.Column("confirmer_email", sa.String(255), nullable=True)
    )
    op.create_foreign_key(
        "fk_reports_task_id", "reports", "report_tasks", ["task_id"], ["id"]
    )
    op.create_index("ix_reports_task_id", "reports", ["task_id"])
    # 幂等：同一任务的同一次触发只允许一个实例（手动实例 task_id 为空不受约束）
    op.create_index(
        "uq_report_task_slot",
        "reports",
        ["task_id", "scheduled_for"],
        unique=True,
        postgresql_where=sa.text("task_id IS NOT NULL"),
        sqlite_where=sa.text("task_id IS NOT NULL"),
    )
    op.create_index(
        "ix_report_pending_confirm", "reports", ["status", "expires_at"]
    )
    # 定时触发的实例没有用户 -> created_by 改可空；
    # status 加宽到 24（pending_confirmation 20 字符，旧的 16 装不下）
    with op.batch_alter_table("reports") as batch:
        batch.alter_column(
            "created_by", existing_type=sa.Integer(), nullable=True
        )
        batch.alter_column(
            "status",
            existing_type=sa.String(length=16),
            type_=sa.String(length=24),
            existing_nullable=False,
        )
    # audit 的操作人同样允许为空（系统触发的记录）；
    # action 加长到 32（新增 generate_ok/generate_fail/resend_confirmation 等长词）
    with op.batch_alter_table("report_audits") as batch:
        batch.alter_column("auditor_id", existing_type=sa.Integer(), nullable=True)
        batch.alter_column(
            "action",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
            existing_nullable=False,
        )

    # ---------------- 清空既有报告数据 ----------------
    # 评审决定：历史数据不可用、不考虑兼容。顺序遵守外键依赖。
    op.execute("DELETE FROM push_records")
    op.execute("DELETE FROM report_audits")
    op.execute("DELETE FROM reports")


def downgrade() -> None:
    op.execute("DELETE FROM push_records")
    op.execute("DELETE FROM report_audits")
    op.execute("DELETE FROM reports")

    # 注意顺序：batch_alter_table 在 PG 上会重建表并丢失其上的索引，
    # 因此必须**先显式删索引**，再改列。
    op.drop_index("ix_report_pending_confirm", table_name="reports")
    op.drop_index("uq_report_task_slot", table_name="reports")
    op.drop_index("ix_reports_task_id", table_name="reports")
    op.drop_constraint("fk_reports_task_id", "reports", type_="foreignkey")

    with op.batch_alter_table("report_audits") as batch:
        batch.alter_column("auditor_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("reports") as batch:
        batch.alter_column("created_by", existing_type=sa.Integer(), nullable=False)
        batch.alter_column(
            "status",
            existing_type=sa.String(length=24),
            type_=sa.String(length=16),
            existing_nullable=False,
        )
        batch.drop_column("confirmer_email")
        batch.drop_column("last_reminder_at")
        batch.drop_column("confirmed_at")
        batch.drop_column("expires_at")
        batch.drop_column("confirmation_sent_at")
        batch.drop_column("confirmation_token")
        batch.drop_column("source")
        batch.drop_column("scheduled_for")
        batch.drop_column("task_id")

    op.drop_index("uq_task_name_active", table_name="report_tasks")
    op.drop_table("report_tasks")
