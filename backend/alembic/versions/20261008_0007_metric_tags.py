"""metric tags

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-08

指标加 tags 列（JSONB）+ GIN 索引；仅用于展示与搜索。
host_ids 的历史数据修复不在本迁移内完成（需调用 Zabbix API），
由一次性脚本处理。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type() -> sa.types.TypeEngine:
    """与模型一致的跨库 JSON 类型（PG=JSONB，其它=JSON），每次新建实例。"""
    return sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.add_column(
        "metric_definitions", sa.Column("tags", _json_type(), nullable=True)
    )
    # 既有数据回填为空数组，并收紧为 NOT NULL（与模型 default=list 对齐）
    op.execute("UPDATE metric_definitions SET tags = '[]'::jsonb WHERE tags IS NULL")
    op.alter_column(
        "metric_definitions",
        "tags",
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )
    # GIN 索引支撑 tags @> '["核心"]'::jsonb 的包含查询
    op.create_index(
        "ix_metric_definitions_tags_gin",
        "metric_definitions",
        ["tags"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_metric_definitions_tags_gin", table_name="metric_definitions")
    op.drop_column("metric_definitions", "tags")
