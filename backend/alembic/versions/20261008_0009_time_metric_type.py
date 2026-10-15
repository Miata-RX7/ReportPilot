"""time 指标类型

- metric_type 的 CHECK 约束加入 'time'
- data_source_id 改为可空（时间类型不取数，与数据源无关）

只做 DDL，不含数据修复：既有指标都是取数类型，data_source_id 已有值，
放宽 NOT NULL 不影响存量数据。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

METRIC_TYPES = ("scalar", "curve", "pie", "text", "time")


def upgrade() -> None:
    # 时间类型不取数 -> 允许无数据源
    with op.batch_alter_table("metric_definitions") as batch:
        batch.alter_column(
            "data_source_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
    # metric_type 白名单扩容
    op.drop_constraint("ck_metric_type", "metric_definitions", type_="check")
    op.create_check_constraint(
        "ck_metric_type",
        "metric_definitions",
        f"metric_type IN ({', '.join(repr(t) for t in METRIC_TYPES)})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_metric_type", "metric_definitions", type_="check")
    op.create_check_constraint(
        "ck_metric_type",
        "metric_definitions",
        "metric_type IN ('scalar', 'curve', 'pie', 'text')",
    )
    # 收回可空前，先给 time 类型填一个数据源占位，避免降级后出现悬挂 NULL
    op.execute(
        "UPDATE metric_definitions SET data_source_id = "
        "(SELECT MIN(id) FROM data_sources) "
        "WHERE data_source_id IS NULL AND metric_type = 'time'"
    )
    with op.batch_alter_table("metric_definitions") as batch:
        batch.alter_column(
            "data_source_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
