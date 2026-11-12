"""公开确认接口（**无需登录**）：凭邮件里的一次性 token 确认并触发推送。

安全要点：
- 路由不带 get_current_user 依赖，确认人不需要平台账号
- token 一次性：确认成功后立即清空（库里只有 sha256 哈希）
- 预览接口**不返回具体指标数值**，避免链接转发导致数据外泄
- 并发点击靠 SELECT ... FOR UPDATE 保证只有一个成功
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.template import WordTemplate
from app.models.report import Report
from app.models.template import TemplateVersion
from app.schemas.report import ConfirmPreview, ConfirmRequest, ConfirmResult
from app.services import report_service
from app.services.docx_renderer import open_template, template_variables
from app.services.report_service import ReportError
from app.services.task_service import token_matches

DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

logger = logging.getLogger(__name__)

router = APIRouter()

ABNORMAL_THRESHOLD = 10.0  # |change_pct| > 10% 视为异常（与前端一致）


def _state_of(report: Report, token: str) -> tuple[str, str | None]:
    """判定 token 状态（valid/invalid/used/expired）与提示文案。

    token 是否已被消费**优先于**状态判断：确认成功后 token 即清空，
    此时用户重复打开链接应看到"该链接已使用"，而不是"状态不支持确认"。
    """
    if report.confirmation_token is None:
        # 已确认过（token 已清空）——重复打开就是"已使用"
        return "used", "该链接已使用"
    if not token_matches(token, report.confirmation_token):
        return "invalid", "确认链接无效"
    if report.status != "pending_confirmation":
        return "invalid", f"当前状态（{report.status}）不支持确认"
    expires = report_service._aware(report.expires_at)
    if expires is not None and expires < datetime.now(timezone.utc):
        return "expired", "确认链接已过期，请联系管理员重新发送"
    return "valid", None


def _summary(report: Report) -> tuple[str | None, int, int]:
    """AI 摘要文本 + 指标数 + 异常数（**不含具体数值**）。"""
    insights = report.insights_json or {}
    summary_text = None
    for v in insights.values():
        if isinstance(v, dict) and v.get("text"):
            summary_text = str(v["text"])
            break
    facts = report.facts_json or []
    abnormal = 0
    for f in facts:
        pct = f.get("change_pct")
        if isinstance(pct, (int, float)) and abs(float(pct)) > ABNORMAL_THRESHOLD:
            abnormal += 1
    return summary_text, len(facts), abnormal


async def _summary_labels(db: AsyncSession, report: Report) -> dict[str, str]:
    """总结类占位的显示名 {占位名: 显示名}。

    显示名存在**模板**上（word_templates.summary_labels_json）—— 它是业务语义，
    不随模板版本变化。没配显示名的占位不出现在这里，前端回退显示占位名。
    """
    if report.template_id is None:
        return {}
    tpl = await db.get(WordTemplate, report.template_id)
    if tpl is None:
        return {}
    labels = dict(tpl.summary_labels_json or {})
    names = await _summary_placeholders(db, report)
    return {k: v for k, v in labels.items() if k in names and v}


async def _summary_placeholders(db: AsyncSession, report: Report) -> list[str]:
    """报告模板里声明的「总结_」占位。

    这些占位**不参与模板绑定**（模板管理页不要求绑定），由确认人在确认页上传
    文件后生成、确认时快照、渲染时注入。所以要把它们告诉确认人，让 TA 知道能把
    总结填到哪一处。
    """
    from app.services.docx_renderer import is_summary_placeholder, template_variables

    if report.version_id is None:
        return []
    version = await db.get(TemplateVersion, report.version_id)
    if version is None or not version.file_path:
        return []
    try:
        tpl = open_template(Path(version.file_path))
    except Exception:
        return []
    return sorted(v for v in template_variables(tpl) if is_summary_placeholder(v))


@router.get("/{report_id}", response_model=ConfirmPreview)
async def preview_confirmation(
    report_id: int,
    token: str,
    db: AsyncSession = Depends(get_db),
) -> ConfirmPreview:
    """确认页概要：报告名/周期/AI 摘要/指标数 + token 有效性。"""
    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告不存在")
    name = await report_service.report_display_name(db, report)
    state, message = _state_of(report, token)
    summary, count, abnormal = _summary(report)
    return ConfirmPreview(
        summary_placeholders=await _summary_placeholders(db, report),
        summary_placeholder_labels=await _summary_labels(db, report),
        docx_downloaded_at=report.docx_downloaded_at,
        report_id=report.id,
        report_name=name,
        period=report.period,
        status=report.status,
        confirmer_email=report.confirmer_email or "",
        recipient_emails=list(report.recipient_emails or []),
        created_at=report.created_at,
        expires_at=report.expires_at,
        is_expired=state == "expired",
        ai_summary=summary,
        metric_count=count,
        abnormal_count=abnormal,
        token_state=state,
        message=message,
    )


@router.post("/{report_id}/confirm-only")
async def confirm_only_report(
    report_id: int,
    payload: ConfirmRequest,
    db: AsyncSession = Depends(get_db),
) -> ConfirmResult:
    """**仅确认，不发送** —— 本轮报告自动化任务就此结束。

    场景：确认人下载了报告、线下改完自己发给收件人了。点这个按钮就是告诉系统
    「我处理完了」，系统不再发送、也不再催办。不可撤销。
    """
    exists = await db.scalar(select(Report.id).where(Report.id == report_id))
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告不存在")
    try:
        await report_service.confirm_only(
            report_id,
            payload.token,
            summaries=[s.model_dump() for s in payload.summaries],
        )
    except ReportError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return ConfirmResult(
        report_id=report_id,
        confirmed=True,
        push_status="not_pushed",
        message="已确认，本轮报告任务已结束（未通过系统发送）",
    )


@router.post("/{report_id}/downloaded")
async def mark_docx_downloaded(
    report_id: int,
    token: str = Query(..., description="确认链接的一次性 token"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """记录「确认人下载过 Word 版」。

    下载是浏览器行为，后端默认不知道 —— 让前端在点击下载时上报一次。
    记在服务器上而不是 localStorage：刷新页面、换浏览器、甚至链接被转发，
    提醒都还在。
    """
    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告不存在")
    if not token_matches(token, report.confirmation_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "确认链接无效")
    report.docx_downloaded_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}


@router.get("/{report_id}/preview.pdf")
async def preview_report_pdf(
    report_id: int,
    token: str = Query(..., description="确认链接的一次性 token"),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """报告 PDF 预览（免登录，凭 token）。

    预览的就是**最终发出的那份** —— 生成时转的 PDF，重渲染后同步更新。
    """
    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告不存在")
    if not token_matches(token, report.confirmation_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "确认链接无效")
    if not report.pdf_path or not Path(report.pdf_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告预览不可用（未生成 PDF）")
    return FileResponse(
        report.pdf_path,
        media_type="application/pdf",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{report_id}/download.docx")
async def download_report_docx(
    report_id: int,
    token: str = Query(..., description="确认链接的一次性 token"),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """下载报告 Word 版（免登录，凭 token）。

    确认人下载后通常会线下改 —— 所以下载动作会**上报**（见 /downloaded），
    页面据此提醒「您可能已线下发送，请点『仅确认不发送』结束本轮任务」。
    """
    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告不存在")
    if not token_matches(token, report.confirmation_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "确认链接无效")
    if not report.file_path or not Path(report.file_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告文件不存在")
    tpl = await db.get(WordTemplate, report.template_id)
    filename = f"{tpl.name if tpl else 'report'}_{report.period}.docx"
    return FileResponse(
        report.file_path,
        media_type=DOCX_MIME,
        filename=filename,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{report_id}", response_model=ConfirmResult)
async def confirm_report(
    report_id: int,
    payload: ConfirmRequest,
    db: AsyncSession = Depends(get_db),
) -> ConfirmResult:
    """确认并立即推送给最终收件人。"""
    # 只校验报告存在（真正的锁与状态判定在 service 内部的事务里做）
    exists = await db.scalar(select(Report.id).where(Report.id == report_id))
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告不存在")
    try:
        push_status, err = await report_service.confirm(
            report_id,
            payload.token,
            summaries=[s.model_dump() for s in payload.summaries],
        )
    except ReportError as exc:
        # 明确报错（链接已使用 / 无效 / 过期），不静默失败
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    if push_status == "pushed":
        return ConfirmResult(
            report_id=report_id,
            confirmed=True,
            push_status="pushed",
            message="已确认，报告已发送给收件人",
        )
    return ConfirmResult(
        report_id=report_id,
        confirmed=True,
        push_status="push_failed",
        push_error=err,
        message="已确认，但推送失败，请联系管理员重试",
    )
