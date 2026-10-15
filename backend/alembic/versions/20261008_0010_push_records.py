"""推送记录表

- push_records：报告邮件的每次发送尝试（成功/失败都记）
- 纯新增表，不影响既有数据
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "push_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("action", sa.String(16), nullable=False, server_default="push"),
        sa.Column("status", sa.String(16), nullable=False, server_default="success"),
        sa.Column("recipients", _json_type(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attachment_name", sa.String(255), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
    )
    op.create_index("ix_push_records_report_id", "push_records", ["report_id"])
    op.create_index(
        "ix_push_record_report", "push_records", ["report_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_push_record_report", table_name="push_records")
    op.drop_index("ix_push_records_report_id", table_name="push_records")
    op.drop_table("push_records")
