"""公开确认接口（**无需登录**）：凭邮件里的一次性 token 确认并触发推送。

安全要点：
- 路由不带 get_current_user 依赖，确认人不需要平台账号
- token 一次性：确认成功后立即清空（库里只有 sha256 哈希）
- 预览接口**不返回具体指标数值**，避免链接转发导致数据外泄
- 并发点击靠 SELECT ... FOR UPDATE 保证只有一个成功
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.report import Report
from app.schemas.report import ConfirmPreview, ConfirmRequest, ConfirmResult
from app.services import report_service
from app.services.report_service import ReportError
from app.services.task_service import token_matches

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
        push_status, err = await report_service.confirm(report_id, payload.token)
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
