"""给 metric_definitions 加 owner_id（指标归属到创建人）。

背景（2026-11-11）：指标原先**完全不分用户** —— 所有人都能看到全部指标，
但只有 admin 能改。现在改为「每个人只看到自己创建的指标」。

为什么加 owner_id 而不是复用 created_by：指标表原本没有用户字段。

**存量数据判给 ops（id=2）**：用户明确要求。这样存量 67 条指标（含 time 类型）
只有 ops 和 admin 能改、能看见；普通用户从零开始积累自己的指标。

owner_id 可空，语义：
  - NULL    -> 公共指标（所有登录用户可见，只读；新建指标不会产生 NULL）
  - = 用户 id -> 该用户的私有指标

Revision ID: 0023
Revises: 0022
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str = "0022"
branch_labels = None
depends_on = None

#: 存量指标的归属人
OWNER_USERNAME = "ops"


def upgrade() -> None:
    op.add_column(
        "metric_definitions",
        sa.Column("owner_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_metric_definitions_owner_id",
        "metric_definitions",
        ["owner_id"],
    )
    # 存量判给 ops（用 DO 块，找不到 ops 时静默跳过而不是报错）
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM users WHERE username = 'ops') THEN
                UPDATE metric_definitions
                SET owner_id = (SELECT id FROM users WHERE username = 'ops')
                WHERE owner_id IS NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_metric_definitions_owner_id", table_name="metric_definitions")
    op.drop_column("metric_definitions", "owner_id")
