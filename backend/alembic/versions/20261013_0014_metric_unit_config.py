"""指标单位换算配置（metric_definitions.unit_config）

- 为指标增加单位换算列：纯比例的「展示派生层」，把 262468.9041666667 bps 统一展示为
  262.47 Kbps。事实层（value / baseline_value / change_pct）与聚合口径完全不受影响。
- 该列为 NULL 时行为与升级前完全一致（不换算），存量指标零漂移。
- 仅 scalar / curve 生效；pie / text / time 若配置换算，写路径（API + Pydantic）拒绝。

Revision ID: 0014
Revises: 0013
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

json_variant = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "metric_definitions",
        sa.Column("unit_config", json_variant, nullable=True),
    )


def downgrade() -> None:
    # 纯展示配置，无数据回填需求：直接删列
    op.drop_column("metric_definitions", "unit_config")