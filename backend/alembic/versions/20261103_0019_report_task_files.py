"""确认人上传的本期数据文件 + 总结快照

需求（2026-11-03）：报告任务里有两个小节 ——
- 小节 1：明细表（**放在邮件附件里**，不进正文）
- 小节 2：对该数据的分类统计总结（格式每月不变，只有数字变）

实现路径：确认人在**确认页面**上传 Excel -> 平台用 AI 做分类统计 ->
人工可编辑 -> 确认时快照 -> 报告渲染时把总结填进 {{ 总结_xxx }} 占位 ->
推送邮件带上原始 Excel 作为附件。

新增两张表：
- report_task_files：上传的文件本体（属于某一期报告）
- report_summaries：确认时的总结快照（文本 + 溯源数据 + 校验结果）

**为什么总结要快照**：确认后文件理论上仍可被替换。若渲染时读实时文件，
最终报告的内容就与确认人确认过的不是一回事 —— 对有审计链的报告是硬伤。

Revision ID: 0019
Revises: 0018
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "report_task_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("stored_path", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("columns_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["report_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_report_task_files_report_id", "report_task_files", ["report_id"])
    op.create_index("ix_report_task_files_task_id", "report_task_files", ["task_id"])
    op.create_index("ix_report_task_files_sha256", "report_task_files", ["sha256"])

    op.create_table(
        "report_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("placeholder", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("categories_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("verification_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["file_id"], ["report_task_files.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_report_summaries_report_id", "report_summaries", ["report_id"])
    op.create_index("ix_report_summaries_file_id", "report_summaries", ["file_id"])
    op.create_index(
        "ix_report_summary_report_placeholder",
        "report_summaries",
        ["report_id", "placeholder"],
    )


def downgrade() -> None:
    op.drop_index("ix_report_summary_report_placeholder", table_name="report_summaries")
    op.drop_index("ix_report_summaries_file_id", table_name="report_summaries")
    op.drop_index("ix_report_summaries_report_id", table_name="report_summaries")
    op.drop_table("report_summaries")
    op.drop_index("ix_report_task_files_sha256", table_name="report_task_files")
    op.drop_index("ix_report_task_files_task_id", table_name="report_task_files")
    op.drop_index("ix_report_task_files_report_id", table_name="report_task_files")
    op.drop_table("report_task_files")
