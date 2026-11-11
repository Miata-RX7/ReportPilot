"""新建 ownership_transfers（离职交接审计）。

背景（2026-11-11）：指标/任务按创建人隔离后，人离职需要把数据转给接手人。
交接**不可逆**，必须留痕：谁、何时、把谁的数据转给了谁、各多少条。

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ownership_transfers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("from_user_id", sa.Integer(), nullable=False),
        sa.Column("to_user_id", sa.Integer(), nullable=False),
        # FK 直接写在建表里 —— 不用 op.create_foreign_key（那个要 6 个位置参数，
        # 写漏了会报 missing 'remote_cols'）
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name="fk_ownership_transfers_actor", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["from_user_id"], ["users.id"], name="fk_ownership_transfers_from"
        ),
        sa.ForeignKeyConstraint(
            ["to_user_id"], ["users.id"], name="fk_ownership_transfers_to"
        ),
        sa.Column("metrics_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tasks_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("templates_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("grants_deleted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details_json", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_ownership_transfers_actor_id", "ownership_transfers", ["actor_id"])
    op.create_index("ix_ownership_transfers_from_user_id", "ownership_transfers", ["from_user_id"])
    op.create_index("ix_ownership_transfers_to_user_id", "ownership_transfers", ["to_user_id"])


def downgrade() -> None:
    op.drop_table("ownership_transfers")
