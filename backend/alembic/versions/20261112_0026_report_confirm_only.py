"""reports 加「仅确认不发送」与报告预览相关字段（2026-11-12）。

背景：确认页原本只有「确认并发送」一个出口。用户会下载报告、线下改完自己发，
系统就永远卡在 pending_confirmation（还会继续催办、可能重复发送）。

新增：
- ``confirmed_no_push_at``：点了「仅确认不发送」的时间（终态标记）
- ``docx_downloaded_at``：确认页下载过 Word 版的时间 —— 用来提醒「您可能已线下
  修改并发送，请点『仅确认不发送』结束本轮」
- ``pdf_path``：报告 PDF 预览缓存（生成时由 ONLYOFFICE converter 转一次）
- ``final_file_path``：预留 —— 将来若支持「上传改好的文件由系统发送」，放最终版

status 新增终态 ``confirmed_no_push``（reports.status 无 check 约束，不用改约束）。

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("confirmed_no_push_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reports", sa.Column("docx_downloaded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reports", sa.Column("pdf_path", sa.String(512), nullable=True))
    op.add_column("reports", sa.Column("final_file_path", sa.String(512), nullable=True))
    op.create_index("ix_reports_pdf_path", "reports", ["pdf_path"])


def downgrade() -> None:
    op.drop_index("ix_reports_pdf_path", table_name="reports")
    op.drop_column("reports", "final_file_path")
    op.drop_column("reports", "pdf_path")
    op.drop_column("reports", "docx_downloaded_at")
    op.drop_column("reports", "confirmed_no_push_at")
