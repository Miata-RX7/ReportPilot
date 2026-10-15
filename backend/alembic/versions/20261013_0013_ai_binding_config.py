"""模板 AI 摘要槽位配置（template_bindings.ai_config_json）

- 为「AI 摘要」绑定增加 AI 写作配置列：提示词 / 参考范文 / 字数上限 / 温度。
- 同时把旧的固定槽位 key 收敛规则改为「缺省 = placeholder 名」：把历史上
  binding_type='insight' 且 binding_key='summary' 的行改写为各自的 placeholder 名，
  使「一个 AI 占位符 = 一段独立摘要」（多段摘要）真正生效。
- 该列为 NULL 时行为与升级前完全一致（固定指令、200 字、无范文）。

Revision ID: 0013
Revises: 0012
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

json_variant = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "template_bindings",
        sa.Column("ai_config_json", json_variant, nullable=True),
    )
    # 旧 insight 槽位 key 固定为 'summary' -> 改写为 placeholder 名（天然唯一）
    op.execute(
        "UPDATE template_bindings SET binding_key = placeholder "
        "WHERE binding_type = 'insight' AND binding_key = 'summary'"
    )


def downgrade() -> None:
    # 多段摘要回退后共用一个槽位，统一收敛为旧默认值 'summary'
    op.execute(
        "UPDATE template_bindings SET binding_key = 'summary' "
        "WHERE binding_type = 'insight'"
    )
    op.drop_column("template_bindings", "ai_config_json")