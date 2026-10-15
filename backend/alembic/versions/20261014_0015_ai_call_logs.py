"""AI 调用留痕（ai_call_logs）

回答「每一次请求 AI 的具体参数、prompt 是什么」：把真正送出去的 messages、
模型参数（model / temperature / max_tokens / timeout / base_url）、响应原文、
护栏重试的第二次请求与耗时落库。

- 覆盖 AI 的三个入口：报告生成 / 单段重生成 / 模板试跑预览（试跑不落报告，
  但同样留痕）
- 不存 api_key；base_url 去 userinfo，error 文本脱敏（红线：只写不读密钥）
- 全新表，不触碰任何存量数据

Revision ID: 0015
Revises: 0014
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

json_variant = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "ai_call_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "report_id",
            sa.Integer(),
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "template_id",
            sa.Integer(),
            sa.ForeignKey("word_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="generate"),
        sa.Column("slot", sa.String(length=128), nullable=True),
        sa.Column("period", sa.String(length=16), nullable=True),
        sa.Column(
            "actor_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("base_url", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("timeout_seconds", sa.Float(), nullable=True),
        sa.Column(
            "has_custom", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("messages", json_variant, nullable=False),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="success"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retried", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retry_messages", json_variant, nullable=True),
        sa.Column("retry_response_text", sa.Text(), nullable=True),
        sa.Column("retry_error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_ai_call_logs_report_id", "ai_call_logs", ["report_id"])
    op.create_index("ix_ai_call_logs_template_id", "ai_call_logs", ["template_id"])
    # 两个主要查询方向：某份报告的各段请求 / 某个模板的试跑历史
    op.create_index("ix_ai_call_report", "ai_call_logs", ["report_id", "id"])
    op.create_index("ix_ai_call_template", "ai_call_logs", ["template_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_ai_call_template", table_name="ai_call_logs")
    op.drop_index("ix_ai_call_report", table_name="ai_call_logs")
    op.drop_index("ix_ai_call_logs_template_id", table_name="ai_call_logs")
    op.drop_index("ix_ai_call_logs_report_id", table_name="ai_call_logs")
    op.drop_table("ai_call_logs")