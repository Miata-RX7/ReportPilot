"""ownership_transfers 去掉对 users 的外键约束（2026-11-12）。

背景：审计表 ``ownership_transfers`` 记录「谁把什么交接给了谁」。
原来的三个外键（actor_id / from_user_id / to_user_id -> users.id）会导致
**用户一旦被物理删除，删除操作直接失败**（ForeignKeyViolationError）——
而离职交接的典型流程恰恰是「交接完就删人」。

审计记录的价值恰恰在于「人已经走了，但交接过程有据可查」，所以：
**id 只作为普通整数留存，不再建立外键约束**。用户被删除后，审计记录仍完整
保留当时的 id。

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str = "0026"
branch_labels = None
depends_on = None

_CONSTRAINTS = (
    "fk_ownership_transfers_actor",
    "fk_ownership_transfers_from",
    "fk_ownership_transfers_to",
)


def upgrade() -> None:
    for name in _CONSTRAINTS:
        op.drop_constraint(name, "ownership_transfers", type_="foreignkey")


def downgrade() -> None:
    op.create_foreign_key(
        "fk_ownership_transfers_actor",
        "ownership_transfers",
        "users",
        ["actor_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_ownership_transfers_from",
        "ownership_transfers",
        "users",
        ["from_user_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_ownership_transfers_to",
        "ownership_transfers",
        "users",
        ["to_user_id"],
        ["id"],
    )
