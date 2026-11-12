"""放开 metric_definitions 的 metric_type 检查约束，加入 calculated。

背景（2026-11-12）：新增「计算型指标」（metric_type=calculated），DB 层
``ck_metric_type`` 只允许 scalar/curve/pie/text/time -> 插入直接被
CheckViolation 拒绝（HTTP 500）。

注意：改的是**约束**，不是列类型 —— 列还是 varchar。

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str = "0024"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_metric_type"
# ARRAY[...] 里要是一串**元素**，不能是元组 —— 写成 ARRAY[('a','b')] 会变成
# 「一个元素是数组」的嵌套数组，导致**所有行都不匹配**、约束加不上
_ALLOWED = "['scalar', 'curve', 'pie', 'text', 'time', 'calculated']"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "metric_definitions", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "metric_definitions",
        "metric_type::text = ANY (ARRAY[%s]::text[])" % _ALLOWED,
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "metric_definitions", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "metric_definitions",
        "metric_type::text = ANY (ARRAY['scalar', 'curve', 'pie', 'text', 'time']::text[])",
    )
