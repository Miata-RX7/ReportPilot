"""确认页的数据文件与总结接口（**无需登录**）。

安全要点：
- 路由不带 get_current_user，确认人不需要平台账号
- 每次调用都校验邮件里的一次性 token（与确认动作同一个 token）
- 上传**只写不读**：文件仅属于该期报告，别的 token 拿不到
- 类型白名单（只收 xlsx/xlsm）+ 大小上限（20MB）
"""

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.report import Report
from app.schemas.report import (
    ConfirmPreview,
    ConfirmRequest,
    ConfirmResult,
    ReportFileRead,
    ReportSummaryRead,
    SummaryDraftRead,
)
from app.services import report_service, report_summary_service
from app.services.report_service import ReportError
from app.services.report_summary_service import SummaryError
from app.services.task_service import token_matches

logger = logging.getLogger(__name__)

router = APIRouter()


async def _report_or_404(db: AsyncSession, report_id: int) -> Report:
    """取报告，不存在则 404。

    注意必须 ``await db.get(...)`` —— AsyncSession.get 是协程，忘了 await 会拿到
    一个 coroutine 对象而不是行，后续访问属性直接 500（实测踩过）。
    """
    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告不存在")
    return report


async def report_template_placeholders(db: AsyncSession, report: Report) -> list[str]:
    """报告所用模板当前版本里声明的占位。"""
    if report.template_id is None:
        return []
    tpl = await db.get(WordTemplate, report.template_id)
    if tpl is None:
        return []
    version = await db.get(TemplateVersion, tpl.current_version_id)
    return list(version.placeholders_json or []) if version else []


def _check_token(report: Report, token: str) -> None:
    """校验一次性 token。确认成功后 token 即失效，这里同样拒绝。"""
    if report.confirmation_token is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "该链接已使用")
    if not token_matches(token, report.confirmation_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "确认链接无效")


@router.get("/{report_id}/files", response_model=list[ReportFileRead])
async def list_report_files(
    report_id: int,
    token: str,
    db: AsyncSession = Depends(get_db),
) -> list[ReportFileRead]:
    """确认页列出已上传的文件与已生成的总结。"""
    report = await _report_or_404(db, report_id)
    _check_token(report, token)
    files = await report_summary_service.list_files(db, report_id)
    summaries = await report_summary_service.list_summaries(db, report_id)
    by_file: dict[int, ReportSummaryRead] = {}
    for s in summaries:
        by_file[s.file_id] = ReportSummaryRead(
            placeholder=s.placeholder,
            prompt=s.prompt,
            text=s.text,
            categories=s.categories_json,
            verification=s.verification_json,
        )
    out: list[ReportFileRead] = []
    for f in files:
        out.append(
            ReportFileRead(
                id=f.id,
                filename=f.filename,
                size_bytes=f.size_bytes,
                row_count=f.row_count,
                columns=f.columns_json,
                summary=by_file.get(f.id),
            )
        )
    return out


@router.post("/{report_id}/files", response_model=ReportFileRead)
async def upload_report_file(
    report_id: int,
    token: str = Form(...),
    file: UploadFile = File(...),
    placeholder: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> ReportFileRead:
    """确认人上传本期数据文件（Excel）。"""
    report = await _report_or_404(db, report_id)
    _check_token(report, token)
    name = file.filename or "upload.xlsx"
    if not name.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "仅支持 .xlsx / .xlsm 文件"
        )
    data = await file.read()
    try:
        item = await report_summary_service.create_file(
            db, report, filename=name, data=data
        )
    except SummaryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    # **上传时就绑定占位**（一变量一文件）：文件与占位的关系由上传动作确定，
    # 不需要事后用下拉去选 —— 下拉正是「两个文件选到同一占位 -> 静默覆盖」
    # 的来源。
    if placeholder:
        if not is_summary_placeholder(placeholder):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"不是总结类占位: {placeholder}",
            )
        valid = {
            n
            for n in (await report_template_placeholders(db, report))
            if is_summary_placeholder(n)
        }
        if placeholder not in valid:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"模板里没有这个总结占位: {placeholder}",
            )
        await report_summary_service.save_summary(
            db, report, item, placeholder=placeholder, prompt="", text=""
        )
    return ReportFileRead(
        id=item.id,
        filename=item.filename,
        size_bytes=item.size_bytes,
        row_count=item.row_count,
        columns=item.columns_json,
    )


@router.post("/{report_id}/files/{file_id}/summary", response_model=SummaryDraftRead)
async def generate_report_summary(
    report_id: int,
    file_id: int,
    token: str,
    prompt: str = "",
    sheet_name: str | None = None,
    label_col: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> SummaryDraftRead:
    """对某个文件做分类统计，产出总结草稿（**不落库**）。

    刻意不落库：确认人可能反复调整提示词重新生成，满意了再在确认动作里
    提交最终文本。只有确认时写入的才是快照。
    """
    report = await _report_or_404(db, report_id)
    _check_token(report, token)
    files = await report_summary_service.list_files(db, report_id)
    item = next((f for f in files if f.id == file_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在")
    try:
        draft = await report_summary_service.generate_summary(
            db,
            item,
            prompt=prompt,
            sheet_name=sheet_name,
            label_col=label_col,
        )
    except SummaryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    return SummaryDraftRead(
        text=draft.text,
        categories=[c.to_json() for c in draft.categories],
        verification=draft.verification,
        sheet_name=draft.sheet_name,
        label_col=draft.label_col,
        row_count=draft.row_count,
    )

