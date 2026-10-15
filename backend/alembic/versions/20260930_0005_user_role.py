"""user role

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-30

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default 保证既有行回填为 'user'，不触发非空约束
    op.add_column(
        "users",
        sa.Column(
            "role", sa.String(length=16), server_default="user", nullable=False
        ),
    )
    # smoke test seed 的管理员账号提升为 admin
    op.execute("UPDATE users SET role = 'admin' WHERE id = 1")


def downgrade() -> None:
    op.drop_column("users", "role")
