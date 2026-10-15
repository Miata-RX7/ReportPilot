"""metric_type + type_config

- 加 metric_type（默认 scalar，回填所有现有指标）
- 加 type_config（分类型配置容器）
- 加 CHECK 约束保证 metric_type 合法
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

METRIC_TYPES = ("scalar", "curve", "pie", "text")


def _json_type() -> sa.types.TypeEngine:
    """与模型一致的跨库 JSON 类型（PG=JSONB，其它=JSON），每次新建实例。"""
    return sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.add_column(
        "metric_definitions",
        sa.Column("metric_type", sa.String(16), nullable=False, server_default="scalar"),
    )
    op.add_column(
        "metric_definitions",
        sa.Column(
            "type_config", _json_type(), nullable=False, server_default=sa.text("'{}'")
        ),
    )
    # 显式回填（SQLite 等不支持 ALTER ... USING 的方言也能走到这里）
    op.execute(
        "UPDATE metric_definitions SET metric_type = 'scalar' "
        "WHERE metric_type IS NULL OR metric_type = ''"
    )
    op.create_check_constraint(
        "ck_metric_type",
        "metric_definitions",
        f"metric_type IN ({', '.join(repr(t) for t in METRIC_TYPES)})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_metric_type", "metric_definitions", type_="check")
    op.drop_column("metric_definitions", "type_config")
    op.drop_column("metric_definitions", "metric_type")
