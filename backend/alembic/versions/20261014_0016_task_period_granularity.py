"""报告任务周期粒度（report_tasks.period_granularity / period_offset）

周粒度报告：任务可声明统计周期粒度，以及相对触发时刻的周期偏移。

- ``period_granularity``：month（默认，等于升级前行为）/ week
- ``period_offset``：0（默认，触发时刻所在周期）/ -1（上一个周期：
  周一触发取上周、月初触发取上月）

两列都带 server_default，存量任务自动落到 month / 0，
**线上既有任务的周期与行为零变更**。

Revision ID: 0016
Revises: 0015
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "report_tasks",
        sa.Column(
            "period_granularity",
            sa.String(length=8),
            nullable=False,
            server_default="month",
        ),
    )
    op.add_column(
        "report_tasks",
        sa.Column(
            "period_offset",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("report_tasks", "period_offset")
    op.drop_column("report_tasks", "period_granularity")