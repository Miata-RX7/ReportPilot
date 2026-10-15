"""AI 系统提示词版本历史（ai_prompt_versions）

全局两段 system 规则（基础规则 / 追加规则）开放给管理员在「系统设置」页编辑后，
需要一个可信的账本：每次保存 / 恢复默认 / 回滚都追加一行，含操作人快照与变更前后文本。

- append-only：回滚 = 读历史文本 + 写一条新行，不改写已有行
- 当前生效值仍存在app_settings（复用运行时配置覆盖层），本表只管历史
- 全新表，不触碰任何存量数据

Revision ID: 0017
Revises: 0016
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_prompt_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "actor_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "actor_name",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "action",
            sa.String(length=16),
            nullable=False,
            server_default="save",
        ),
        sa.Column("base_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("system_rules", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "replaced_from_id",
            sa.Integer(),
            sa.ForeignKey("ai_prompt_versions.id"),
            nullable=True,
        ),
        sa.Column("previous_base_prompt", sa.Text(), nullable=True),
        sa.Column("previous_system_rules", sa.Text(), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_ai_prompt_version_recent", "ai_prompt_versions", ["id"]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_prompt_version_recent", table_name="ai_prompt_versions")
    op.drop_table("ai_prompt_versions")