"""init

Revision ID: 0001
Revises:
Create Date: 2026-09-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

json_variant = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=128), server_default="", nullable=False),
        sa.Column("source", sa.String(length=16), server_default="local", nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("username"),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "data_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("config_json", json_variant, nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
        sa.Column("secret_hint", sa.String(length=8), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # 软删除友好的部分唯一索引：仅活跃记录之间 name 唯一
    op.create_index(
        "uq_ds_name_active",
        "data_sources",
        ["name"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
        sqlite_where=sa.text("is_active = 1"),
    )

    op.create_table(
        "metric_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("data_source_id", sa.Integer(), nullable=False),
        sa.Column("query_config", json_variant, nullable=False),
        sa.Column("calc_formula", json_variant, nullable=False),
        sa.Column("unit", sa.String(length=32), server_default="", nullable=False),
        sa.Column("period_type", sa.String(length=16), server_default="monthly", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "word_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("placeholders_json", json_variant, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "template_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("placeholder", sa.String(length=128), nullable=False),
        sa.Column("binding_type", sa.String(length=16), nullable=False),
        sa.Column("binding_ref_id", sa.Integer(), nullable=True),
        sa.Column("binding_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["word_templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_template_bindings_template_id", "template_bindings", ["template_id"])

    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("facts_json", json_variant, nullable=False),
        sa.Column("insights_json", json_variant, nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["word_templates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "report_audits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("auditor_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["auditor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_report_audits_report_id", "report_audits", ["report_id"])


def downgrade() -> None:
    op.drop_index("ix_report_audits_report_id", table_name="report_audits")
    op.drop_table("report_audits")
    op.drop_table("reports")
    op.drop_index("ix_template_bindings_template_id", table_name="template_bindings")
    op.drop_table("template_bindings")
    op.drop_table("word_templates")
    op.drop_table("metric_definitions")
    op.drop_index("uq_ds_name_active", table_name="data_sources")
    op.drop_table("data_sources")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
