"""templates module

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "metric_definitions",
        sa.Column("display_precision", sa.Integer(), nullable=True),
    )
    op.add_column(
        "word_templates",
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
    )
    op.drop_constraint("word_templates_name_key", "word_templates", type_="unique")
    op.create_index(
        "uq_tpl_name_active",
        "word_templates",
        ["name"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
        sqlite_where=sa.text("is_active = 1"),
    )
    op.create_unique_constraint(
        "uq_binding_tpl_ph", "template_bindings", ["template_id", "placeholder"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_binding_tpl_ph", "template_bindings", type_="unique")
    op.drop_index("uq_tpl_name_active", table_name="word_templates")
    op.create_unique_constraint("word_templates_name_key", "word_templates", ["name"])
    op.drop_column("word_templates", "is_active")
    op.drop_column("metric_definitions", "display_precision")
