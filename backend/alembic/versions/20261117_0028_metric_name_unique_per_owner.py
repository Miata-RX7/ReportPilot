"""指标重名唯一性从「全局」改为「同一创建人内」（2026-11-17）。

背景：指标按创建人（owner_id）隔离可见，但唯一约束是**全局**的
（``uq_metric_name_active`` 部分唯一索引，只按 ``name``）。于是出现一个
看不懂的错：普通用户建指标撞到别人的同名指标，系统报「指标名称已存在」，
但那个同名指标他**根本看不到**，也不知道是谁的。

改动：唯一作用域改成 ``(owner_id, name)`` —— **同一创建人内不能重名，
不同创建人可以同名**。这样唯一性与可见性对齐了：
- 普通用户只撞得到自己建过的名字 -> 知道是自己重复了
- 不同用户建同名指标 -> 允许（用户明确诉求）
- admin 建同名也不会撞到别人（owner 不同），只撞自己名下的

说明：PostgreSQL 唯一索引里 NULL 互不相等，``owner_id`` 为 NULL 的存量
指标不会互相约束。实测存量 ``owner_id`` 没有 NULL（只有 1 和 2），
且当前不存在任何跨 owner 同名，迁移无需先清理数据。

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_metric_name_active", table_name="metric_definitions")
    op.create_index(
        "uq_metric_owner_name_active",
        "metric_definitions",
        ["owner_id", "name"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
        sqlite_where=sa.text("is_active = 1"),
    )


def downgrade() -> None:
    op.drop_index("uq_metric_owner_name_active", table_name="metric_definitions")
    # 回退到全局唯一：若此时已存在跨 owner 同名，这一步会失败
    op.create_index(
        "uq_metric_name_active",
        "metric_definitions",
        ["name"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
        sqlite_where=sa.text("is_active = 1"),
    )
