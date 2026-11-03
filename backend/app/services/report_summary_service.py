"""确认页上传数据文件 -> AI 分类统计 -> 确认快照 -> 报告重渲染。

链路（2026-11-03）：
1. 报告生成（骨架）-> 邮件发给确认人
2. 确认人在**确认页**上传 Excel -> 平台 AI 做分类统计 -> 人工可编辑
3. 确认时把「文件 + 总结文本」**快照**落库
4. 报告重渲染（把总结填进 {{ 总结_xxx }}）-> 推送，邮件带上原始 Excel 附件

**为什么要快照**：确认后文件理论上仍可被替换。若渲染时读实时文件，最终报告
的内容就与确认人确认过的不是一回事 —— 对有审计链的报告是硬伤。

**为什么明细放附件、不进正文**：明细动辄 200+ 行，塞进 Word 会让文档膨胀到
几十页；正文只放结论，明细作为邮件附件随报告一起发出。
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.docx_renderer import is_summary_placeholder
from app.models.report import Report, ReportSummary, ReportTaskFile
from app.services import file_summary
from app.services.file_summary import Category, FileSummaryError, SheetData

logger = logging.getLogger(__name__)

#: 上传文件大小上限（20MB）—— 明细表可能不小，但也不是无上限
MAX_FILE_BYTES = 20 * 1024 * 1024


class SummaryError(Exception):
    """文件上传 / 总结生成失败。"""


@dataclass
class SummaryDraft:
    """一次总结生成的结果（尚未落库）。"""

    text: str
    categories: list[Category]
    verification: dict[str, Any]
    sheet_name: str
    label_col: str
    row_count: int


def storage_dir() -> Path:
    return Path(get_settings().storage_dir) / "report_files"


# ---------------------------------------------------------------------------
# 文件
# ---------------------------------------------------------------------------


def save_upload(data: bytes, filename: str) -> tuple[Path, str]:
    """落盘并返回 (路径, sha256)。同名同内容不重复占空间。"""
    digest = hashlib.sha256(data).hexdigest()
    root = storage_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{digest[:16]}_{uuid.uuid4().hex[:8]}{Path(filename).suffix.lower()}"
    path.write_bytes(data)
    return path, digest


async def create_file(
    db: AsyncSession,
    report: Report,
    *,
    filename: str,
    data: bytes,
    uploaded_by: int | None = None,
) -> ReportTaskFile:
    """登记一个上传文件（同时把解析出的表结构存下来，前端要展示）。"""
    if len(data) > MAX_FILE_BYTES:
        raise SummaryError(f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)}MB 限制")
    path, digest = save_upload(data, filename)
    columns: list[str] = []
    row_count = 0
    try:
        sheets = file_summary.parse_workbook(path)
        if sheets:
            columns = sheets[0].columns
            row_count = sheets[0].row_count
    except FileSummaryError:
        # 解析失败不阻断上传：用户可能上传了别的 sheet 结构，后面再报错更清晰
        logger.warning("上传文件解析失败（仍保存）: %s", filename)
    item = ReportTaskFile(
        report_id=report.id,
        task_id=report.task_id,
        filename=filename,
        stored_path=str(path),
        sha256=digest,
        size_bytes=len(data),
        row_count=row_count,
        columns_json=columns,
        uploaded_by=uploaded_by,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def list_files(db: AsyncSession, report_id: int) -> list[ReportTaskFile]:
    rows = await db.scalars(
        select(ReportTaskFile)
        .where(ReportTaskFile.report_id == report_id)
        .order_by(ReportTaskFile.id)
    )
    return list(rows.all())


# ---------------------------------------------------------------------------
# 总结生成
# ---------------------------------------------------------------------------


async def generate_summary(
    db: AsyncSession,
    item: ReportTaskFile,
    *,
    prompt: str,
    sheet_name: str | None = None,
    label_col: str | None = None,
) -> SummaryDraft:
    """对文件做分类统计，产出总结文本 + 溯源数据 + 校验结果。"""
    path = Path(item.stored_path)
    if not path.exists():
        raise SummaryError("文件已丢失，无法生成总结")
    sheets = file_summary.parse_workbook(path)
    sheet = _pick_sheet(sheets, sheet_name)
    col = label_col or file_summary.guess_label_column(sheet)
    if not col:
        raise SummaryError("无法确定按哪一列分类，请手动指定")

    from app.services.ai_insight import _call_ai
    from app.services.runtime_config import effective_settings

    settings = effective_settings()
    if not settings.ai_api_key:
        raise SummaryError("AI 未配置 api_key，无法生成总结")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        timeout=settings.ai_timeout_seconds,
    )
    text, cats, verification = await file_summary.summarize_sheet(
        sheet,
        label_col=col,
        prompt=prompt,
        ai_client=client,
        call_ai=_call_ai,
        model=settings.ai_model,
        # 分类统计要的是**准确**而不是文采：温度压低、token 给够
        max_tokens=min(max(2000, len(sheet.rows) * 40), 16000),
        temperature=0.2,
    )
    return SummaryDraft(
        text=text,
        categories=cats,
        verification=verification.to_json(),
        sheet_name=sheet.sheet_name,
        label_col=col,
        row_count=sheet.row_count,
    )


def _pick_sheet(sheets: list[SheetData], sheet_name: str | None) -> SheetData:
    if sheet_name:
        for s in sheets:
            if s.sheet_name == sheet_name:
                return s
        raise SummaryError(f"找不到工作表 {sheet_name}")
    return sheets[0]


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------


async def save_summary(
    db: AsyncSession,
    report: Report,
    item: ReportTaskFile,
    *,
    placeholder: str,
    prompt: str,
    text: str,
    draft: SummaryDraft | None = None,
) -> ReportSummary:
    """写入/更新一条总结快照（同一 report + 占位只留一条）。"""
    # 占位名必须是模板里存在的「总结类」变量（summary_ 前缀）。
    # 存了别的名字（比如中文的「总结_xxx」）会导致：
    # - 它不是模板声明的变量 -> build_context 注入时成为多余 key ->
    #   check_context 判「多余」-> 整份报告渲染失败
    # - 模板里真正的 summary_ 占位拿不到值 -> docxtpl Jinja 抛 UndefinedError
    if not is_summary_placeholder(placeholder):
        raise ValueError(
            f"总结占位名非法: {placeholder!r}"
            "（必须是 summary_ 前缀，且是模板里声明过的变量名）"
        )

    existing = await db.scalar(
        select(ReportSummary).where(
            ReportSummary.report_id == report.id,
            ReportSummary.placeholder == placeholder,
        )
    )
    categories = [c.to_json() for c in (draft.categories if draft else [])]
    verification = draft.verification if draft else {}
    if existing is not None:
        existing.file_id = item.id
        existing.prompt = prompt
        existing.text = text
        existing.categories_json = categories
        existing.verification_json = verification
        await db.commit()
        await db.refresh(existing)
        return existing
    row = ReportSummary(
        report_id=report.id,
        file_id=item.id,
        placeholder=placeholder,
        prompt=prompt,
        text=text,
        categories_json=categories,
        verification_json=verification,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def list_summaries(db: AsyncSession, report_id: int) -> list[ReportSummary]:
    rows = await db.scalars(
        select(ReportSummary)
        .where(ReportSummary.report_id == report_id)
        .order_by(ReportSummary.id)
    )
    return list(rows.all())


async def summary_texts(db: AsyncSession, report_id: int) -> dict[str, str]:
    """渲染用：占位名 -> 总结文本。"""
    rows = await list_summaries(db, report_id)
    return {r.placeholder: r.text for r in rows}


async def files_of_report(db: AsyncSession, report_id: int) -> list[ReportTaskFile]:
    return await list_files(db, report_id)
