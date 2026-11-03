"""给 report_task_files / report_summaries 的 created_at 补 server_default。

背景（2026-11-03 线上故障）：确认页上传 Excel 返回 500。

    NotNullViolationError: null value in column "created_at" of relation
    "report_task_files" violates not-null constraint

根因：迁移 0019 建这两张表时，``created_at`` 只写了 ``nullable=False``，
**漏了 ``server_default=now()``**。而模型侧的 ``TimestampMixin.created_at``
是带 ``server_default=func.now()`` 的 —— 于是 SQLAlchemy 认为「这一列有服务端
默认值，INSERT 时可以不带」，把该列从 INSERT 语句里省略了；而数据库列本身
**没有默认值**可填 -> 插入 NULL -> 违反 NOT NULL。

对比：0001 建的老表都是 ``server_default=sa.func.now()``，所以它们一直正常。

**为什么测试没发现**：测试用 sqlite + ``Base.metadata.create_all``，建表用的是
**模型的** server_default（sqlite 里有默认值），所以测试全绿；只有真库
（按 alembic 迁移建的）才暴露。这是个真实的教训 ——
**ORM 的 create_all 与 alembic 迁移建出来的表可能不一致，测试覆盖不到**。

本迁移把缺的那个默认值补上（只加默认值，不动已有数据）。

Revision ID: 0021
Revises: 0020
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("report_task_files", "report_summaries")


def upgrade() -> None:
    for table in _TABLES:
        op.alter_column(
            table,
            "created_at",
            type_=sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )


def downgrade() -> None:
    for table in _TABLES:
        op.alter_column(
            table,
            "created_at",
            server_default=None,
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
