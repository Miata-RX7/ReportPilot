"""删除 metric_definitions.period_type

这个字段从来就没有生效过：前端「统计周期」下拉一直是disabled 且只有 monthly
一个选项，后端 calc_engine / 报告生成都不读它。留着它真正的代价是 ——
API 接受 `period_type=weekly` 并**静默忽略**，留下「写进去了但不生效」的
假字段，运维时很难判断它到底管什么。

多粒度能力早已落在任务侧（report_tasks.period_granularity / period_offset，
迁移 0016），与本字段无关。

存量已核实（2026-10-19）：全库7 条记录**全部是 monthly**，drop 列零信息损失。
列定义为 `varchar(16) NOT NULL DEFAULT 'monthly'`，无 CHECK 约束、无外键、
无索引引用，因此不存在 NULL 语义或依赖需要迁移。

注意：schema 未开 extra="forbid"，所以老客户端继续传 period_type 不会 422，
只会被 Pydantic 静默丢弃 —— 这是刻意保留的下线友好性。

Revision ID: 0018
Revises: 0017
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("metric_definitions", "period_type")


def downgrade() -> None:
    # 回填时统一给 monthly：这是 drop 前全库的实际取值，不存在信息损失
    op.add_column(
        "metric_definitions",
        sa.Column(
            "period_type",
            sa.String(length=16),
            server_default="monthly",
            nullable=False,
        ),
    )