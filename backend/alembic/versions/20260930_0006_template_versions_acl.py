"""template versions + owner/grants + version-scoped bindings + report version

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-30

既有模板回填为 version 1；绑定迁移到当前版本；报告回填 version_id。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.models.base import JSONVariant

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) word_templates: owner_id / current_version_id
    op.add_column("word_templates", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.add_column(
        "word_templates", sa.Column("current_version_id", sa.Integer(), nullable=True)
    )
    op.create_index("ix_word_templates_owner_id", "word_templates", ["owner_id"])
    op.create_foreign_key(
        "fk_word_templates_owner_id", "word_templates", "users", ["owner_id"], ["id"]
    )

    # 2) template_versions
    op.create_table(
        "template_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "template_id",
            sa.Integer(),
            sa.ForeignKey("word_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("placeholders_json", JSONVariant, nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "source", sa.String(length=16), nullable=False, server_default="upload"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("template_id", "version_no", name="uq_version_tpl_no"),
    )
    op.create_index(
        "ix_template_versions_template_id", "template_versions", ["template_id"]
    )

    # 3) template_grants
    op.create_table(
        "template_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "template_id",
            sa.Integer(),
            sa.ForeignKey("word_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("permission", sa.String(length=8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("template_id", "user_id", name="uq_grant_tpl_user"),
    )
    op.create_index("ix_template_grants_template_id", "template_grants", ["template_id"])
    op.create_index("ix_template_grants_user_id", "template_grants", ["user_id"])

    # 4) 回填版本 1 与属主
    op.execute(
        """
        INSERT INTO template_versions
            (template_id, version_no, file_path, placeholders_json, created_by, source, created_at)
        SELECT id, 1, file_path, placeholders_json, owner_id, 'upload', now()
        FROM word_templates
        """
    )
    op.execute(
        """
        UPDATE word_templates t
        SET current_version_id = v.id
        FROM template_versions v
        WHERE v.template_id = t.id AND v.version_no = 1
        """
    )
    op.execute(
        """
        UPDATE word_templates
        SET owner_id = (SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1)
        WHERE owner_id IS NULL
        """
    )

    # 5) template_bindings: template_id -> version_id
    op.add_column(
        "template_bindings", sa.Column("version_id", sa.Integer(), nullable=True)
    )
    op.execute(
        """
        UPDATE template_bindings b
        SET version_id = v.id
        FROM template_versions v
        WHERE v.template_id = b.template_id AND v.version_no = 1
        """
    )
    op.alter_column("template_bindings", "version_id", nullable=False)
    op.create_foreign_key(
        "fk_template_bindings_version_id",
        "template_bindings",
        "template_versions",
        ["version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_template_bindings_version_id", "template_bindings", ["version_id"]
    )
    op.drop_constraint("uq_binding_tpl_ph", "template_bindings", type_="unique")
    op.drop_column("template_bindings", "template_id")
    op.create_unique_constraint(
        "uq_binding_ver_ph", "template_bindings", ["version_id", "placeholder"]
    )

    # 6) reports.version_id
    op.add_column("reports", sa.Column("version_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_reports_version_id", "reports", "template_versions", ["version_id"], ["id"]
    )
    op.create_index("ix_reports_version_id", "reports", ["version_id"])
    op.execute(
        """
        UPDATE reports r
        SET version_id = t.current_version_id
        FROM word_templates t
        WHERE r.template_id = t.id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_reports_version_id", table_name="reports")
    op.drop_constraint("fk_reports_version_id", "reports", type_="foreignkey")
    op.drop_column("reports", "version_id")

    op.drop_constraint("uq_binding_ver_ph", "template_bindings", type_="unique")
    op.add_column(
        "template_bindings", sa.Column("template_id", sa.Integer(), nullable=True)
    )
    op.execute(
        """
        UPDATE template_bindings b
        SET template_id = v.template_id
        FROM template_versions v
        WHERE b.version_id = v.id
        """
    )
    op.alter_column("template_bindings", "template_id", nullable=False)
    op.create_foreign_key(
        "fk_template_bindings_template_id",
        "template_bindings",
        "word_templates",
        ["template_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_template_bindings_template_id", "template_bindings", ["template_id"]
    )
    op.create_unique_constraint(
        "uq_binding_tpl_ph", "template_bindings", ["template_id", "placeholder"]
    )
    op.drop_index("ix_template_bindings_version_id", table_name="template_bindings")
    op.drop_constraint(
        "fk_template_bindings_version_id", "template_bindings", type_="foreignkey"
    )
    op.drop_column("template_bindings", "version_id")

    op.drop_index("ix_template_grants_user_id", table_name="template_grants")
    op.drop_index("ix_template_grants_template_id", table_name="template_grants")
    op.drop_table("template_grants")

    op.drop_index("ix_template_versions_template_id", table_name="template_versions")
    op.drop_table("template_versions")

    op.drop_constraint("fk_word_templates_owner_id", "word_templates", type_="foreignkey")
    op.drop_index("ix_word_templates_owner_id", table_name="word_templates")
    op.drop_column("word_templates", "current_version_id")
    op.drop_column("word_templates", "owner_id")
