"""给 word_templates 加 summary_labels_json（总结类占位的显示名）。

背景（2026-11-03）：模板里的总结类占位是 ``summary_assets_info`` 这种机器名，
业务人员看不懂。模板管理页给每个总结占位配一个「显示名」（如「资产信息」），
确认页用显示名呈现。

为什么存在**模板**上而不是版本上：显示名是业务语义（这一段叫什么），
不随模板版本变化 —— 改了模板正文，占位名可能变，但显示名是稳定的。

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "word_templates",
        sa.Column(
            "summary_labels_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("word_templates", "summary_labels_json", server_default=None)


def downgrade() -> None:
    op.drop_column("word_templates", "summary_labels_json")
