"""reports module

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

json_variant = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column(
            "recipient_emails", json_variant, nullable=False, server_default="[]"
        ),
    )
    op.add_column(
        "reports", sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("reports", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column(
        "reports", sa.Column("error_stage", sa.String(length=16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("reports", "error_stage")
    op.drop_column("reports", "last_error")
    op.drop_column("reports", "pushed_at")
    op.drop_column("reports", "recipient_emails")
