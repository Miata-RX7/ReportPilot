"""metric soft delete

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "metric_definitions",
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
    )
    op.drop_constraint(
        "metric_definitions_name_key", "metric_definitions", type_="unique"
    )
    op.create_index(
        "uq_metric_name_active",
        "metric_definitions",
        ["name"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
        sqlite_where=sa.text("is_active = 1"),
    )


def downgrade() -> None:
    op.drop_index("uq_metric_name_active", table_name="metric_definitions")
    op.create_unique_constraint(
        "metric_definitions_name_key", "metric_definitions", ["name"]
    )
    op.drop_column("metric_definitions", "is_active")
