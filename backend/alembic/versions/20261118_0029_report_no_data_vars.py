"""报告增加 no_data_vars 字段（部分成功：取不到数的变量清单）。2026-11-18。

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: str = "0028"
branch_labels = None
depends_on = None

# 与 models.base.JSONVariant 对齐：PG 用 JSONB，其它用 JSON
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("no_data_vars", _JSON, nullable=True, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("reports", "no_data_vars")
