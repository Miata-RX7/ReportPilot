"""页面可编辑的运行时配置（app_settings 表）

- 新增 app_settings（key/value）：系统设置页保存的邮件 / AI 配置覆盖 .env 默认值。
- 密钥类字段（smtp_password / ai_api_key）以 Fernet 加密后存入 value。
- 该表为空时行为与升级前完全一致（全部沿用 .env）。

Revision ID: 0012
Revises: 0011
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    op.drop_table("app_settings")