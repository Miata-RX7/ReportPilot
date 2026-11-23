"""角色组（共享圈）+ 取消「公共指标」。

Revision ID: 0030
Revises: 0029
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 角色组
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
    op.create_index("ix_roles_name", "roles", ["name"], unique=True)
    op.create_index("ix_roles_created_by", "roles", ["created_by"])

    op.create_table(
        "role_members",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("added_by", sa.Integer(), nullable=True),
        sa.Column("added_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["added_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("role_id", "user_id"),
    )
    op.create_index("ix_role_members_user", "role_members", ["user_id"])
    op.create_index("ix_role_members_role", "role_members", ["role_id"])

    # 2) 取消「公共指标」：owner_id 改 NOT NULL
    #    先校验没有无主指标（存量应为 0 条）——有的话直接报错，不静默处理。
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM metric_definitions WHERE owner_id IS NULL) THEN
                RAISE EXCEPTION
                    '仍有无主指标（owner_id IS NULL），需先指定归属后再加 NOT NULL 约束';
            END IF;
        END $$;
        """
    )
    op.alter_column(
        "metric_definitions", "owner_id", existing_type=sa.Integer(), nullable=False
    )


def downgrade() -> None:
    op.alter_column(
        "metric_definitions", "owner_id", existing_type=sa.Integer(), nullable=True
    )
    op.drop_index("ix_role_members_role", table_name="role_members")
    op.drop_index("ix_role_members_user", table_name="role_members")
    op.drop_table("role_members")
    op.drop_index("ix_roles_created_by", table_name="roles")
    op.drop_index("ix_roles_name", table_name="roles")
    op.drop_table("roles")
