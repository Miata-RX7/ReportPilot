"""报告实例 API：列表/详情/下载 + 确认相关 + 重跑/重推。

状态机（7 态）见 services/report_service.ALLOWED_ACTIONS。
公开的确认接口在 api/confirmations.py（无需登录）。
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.database import get_db
from app.models.report import PushRecord, Report, ReportAudit
from app.models.template import TemplateVersion, WordTemplate
from app.models.user import User
from app.schemas.report import (
    AuditRead,
    InsightRegenerateRequest,
    InsightSlotResult,
    PushRecordRead,
    ReportCreate,
    ReportDetail,
    ReportListItem,
    ReportRead,
)
from app.services import report_service, template_service
from app.services.report_service import ReportError
from app.services.task_service import report_tz

logger = logging.getLogger(__name__)

router = APIRouter()

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def _get_or_404(db: AsyncSession, report_id: int) -> Report:
    report = await db.get(Report, report_id)
    if report is None:
        from fastapi import HTTPException, status

        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告不存在")
    return report


def _err(exc: Exception) -> "HTTPException":  # type: ignore[name-defined]
    from fastapi import HTTPException, status

    if isinstance(exc, ReportError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.post("", response_model=ReportRead, status_code=201)
async def create_report(
    payload: ReportCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Report:
    """报告页手动创建一次性实例（task_id 为空、source=manual）。

    只创建 pending 实例并立即返回；生成在后台执行，随后进入待确认。
    """
    import asyncio

    from app.services import scheduler

    template = await db.get(WordTemplate, payload.template_id)
    if template is None or not template.is_active:
        from fastapi import HTTPException, status

        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "模板不存在或已停用")
    version = await template_service.get_current_version(db, template)
    if version is None:
        from fastapi import HTTPException, status

        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "模板无可用版本"
        )
    try:
        report = await report_service.create_instance(
            db,
            template=template,
            version=version,
            period=payload.period,
            recipient_emails=list(payload.recipient_emails),
            confirmer_email=payload.confirmer_email,
            source="manual",
            actor_id=current_user.id,
        )
    except ReportError as exc:
        raise _err(exc) from None
    await db.commit()
    report_id = report.id

    # 后台执行生成（线程池，不阻塞请求）
    def _fire() -> None:
        try:
            asyncio.run(report_service.run_generation(report_id, actor_id=current_user.id))
        except Exception as exc:  # pragma: no cover - 后台异常已落库
            logger.exception("手动创建的后台生成失败 report=%d: %s", report_id, exc)

    import threading

    threading.Thread(target=_fire, daemon=True, name=f"gen-{report_id}").start()
    return report


@router.get("", response_model=list[ReportListItem])
async def list_reports(
    report_status: str | None = None,
    period: str | None = None,
    task_id: int | None = Query(default=None, description="按任务过滤"),
    limit: int | None = Query(
        default=None, ge=1, le=200, description="最多返回条数（按 id 倒序取最新）"
    ),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ReportListItem]:
    stmt = (
        select(Report, WordTemplate.name)
        .join(WordTemplate, WordTemplate.id == Report.template_id)
        .order_by(Report.id.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    if report_status:
        stmt = stmt.where(Report.status == report_status)
    if period:
        stmt = stmt.where(Report.period == period)
    if task_id is not None:
        stmt = stmt.where(Report.task_id == task_id)
    rows = (await db.execute(stmt)).all()
    out: list[ReportListItem] = []
    for report, tpl_name in rows:
        # template_name / task_name 不是 ORM 字段，先转 ReportRead 再补进去
        base = ReportRead.model_validate(report).model_dump()
        task_name = None
        if report.task_id is not None:
            from app.models.task import ReportTask

            t = await db.get(ReportTask, report.task_id)
            task_name = t.name if t else f"任务#{report.task_id}"
        out.append(
            ReportListItem(**base, template_name=tpl_name, task_name=task_name)
        )
    return out


@router.get("/{report_id}", response_model=ReportDetail)
async def get_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ReportDetail:
    report = await _get_or_404(db, report_id)
    audits = list(
        (
            await db.scalars(
                select(ReportAudit)
                .where(ReportAudit.report_id == report_id)
                .order_by(ReportAudit.id)
            )
        ).all()
    )
    pushes = list(
        (
            await db.scalars(
                select(PushRecord)
                .where(PushRecord.report_id == report_id)
                .order_by(PushRecord.id)
            )
        ).all()
    )
    tpl = await db.get(WordTemplate, report.template_id)
    name = f"{tpl.name if tpl else '模板'}_{report.period}"
    detail = ReportDetail(
        **ReportRead.model_validate(report).model_dump(),
        facts_json=report.facts_json or [],
        insights_json=report.insights_json or {},
        audits=[AuditRead.model_validate(a) for a in audits],
        push_records=[
            PushRecordRead(
                id=p.id,
                report_id=p.report_id,
                report_name=name,
                attempt=p.attempt,
                action=p.action,
                status=p.status,
                recipients=list(p.recipients or []),
                recipient_count=p.recipient_count,
                attachment_name=p.attachment_name,
                duration_ms=p.duration_ms,
                error=p.error,
                actor_id=p.actor_id,
                created_at=p.created_at,
            )
            for p in pushes
        ],
    )
    return detail


@router.get("/{report_id}/download")
async def download_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> FileResponse:
    from pathlib import Path

    from fastapi import HTTPException, status

    report = await _get_or_404(db, report_id)
    if not report.file_path or not Path(report.file_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告文件不存在")
    tpl = await db.get(WordTemplate, report.template_id)
    return FileResponse(
        report.file_path,
        media_type=DOCX_MIME,
        filename=f"{tpl.name if tpl else 'report'}_{report.period}.docx",
    )


@router.post("/{report_id}/resend-confirmation", status_code=204)
async def resend_confirmation(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> None:
    """重新发送确认邮件（换新 token、重置过期时间）。"""
    await _get_or_404(db, report_id)
    try:
        await report_service.resend_confirmation(report_id)
    except ReportError as exc:
        raise _err(exc) from None


@router.post("/{report_id}/confirm", response_model=ReportRead)
async def confirm_report_in_platform(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
) -> ReportRead:
    """**在平台内确认**（不必点邮件链接）。

    两种身份，走两条审计：
    - **管理员** → `confirm_by_admin`（代确认，任何待确认实例）
    - **该任务的确认人本人**（登录邮箱 == 任务的 confirmer_email）→ `confirm`

    其余登录用户 403。两者都允许对**已过期**的链接确认（过期限制的是链接，不是权限）。
    """
    report = await _get_or_404(db, report_id)
    from fastapi import HTTPException, status

    is_admin = current.role == "admin"
    is_confirmer = (current.email or "").lower() == (report.confirmer_email or "").lower()
    if not is_admin and not is_confirmer:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "只有管理员或该任务的确认人本人可以确认此报告"
        )

    try:
        if is_admin:
            push_status, err = await report_service.confirm_by_admin(
                report_id, actor_id=current.id
            )
            how = "管理员代确认"
        else:
            push_status, err = await report_service.confirm_by_user(
                report_id, actor_id=current.id
            )
            how = "确认人本人确认"
    except ReportError as exc:
        # 状态冲突用 409（与公开确认接口 /api/confirm/{id} 语义一致）
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None

    logger.info(
        "%s id=%s by=%s -> %s%s",
        how,
        report_id,
        current.username,
        push_status,
        f" ({err})" if err else "",
    )
    report = await db.get(Report, report_id)
    assert report is not None  # _get_or_404 已保证存在
    return ReportRead.model_validate(report)


@router.post("/{report_id}/retry-push", response_model=ReportRead)
async def retry_push(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Report:
    await _get_or_404(db, report_id)
    try:
        return await report_service.retry_push(report_id, actor_id=current_user.id)
    except ReportError as exc:
        raise _err(exc) from None


@router.post("/{report_id}/regenerate", response_model=ReportRead, status_code=202)
async def regenerate_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Report:
    """failed -> 重新生成（后台执行，立即返回）。"""
    import asyncio
    import threading

    report = await _get_or_404(db, report_id)
    try:
        report_service._ensure_action(report, "regenerate")
    except ReportError as exc:
        raise _err(exc) from None

    def _fire() -> None:
        try:
            asyncio.run(
                report_service.regenerate(report_id, actor_id=current_user.id)
            )
        except Exception as exc:  # pragma: no cover
            logger.exception("重新生成失败 report=%d: %s", report_id, exc)

    threading.Thread(target=_fire, daemon=True, name=f"regen-{report_id}").start()
    return report


@router.post(
    "/{report_id}/insights/{slot}/regenerate", response_model=InsightSlotResult
)
async def regenerate_insight_slot(
    report_id: int,
    slot: str,
    payload: InsightRegenerateRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InsightSlotResult:
    """只重算某一段 AI 摘要并重渲染 docx（facts 用报告快照，不重算指标）。

    适用于已生成的报告：改了提示词/范文后刷新这一段文字，无需重跑整篇。
    - 传 `ai_config` 可**临时覆盖**（页面改完先试后存），不传则用模板当前绑定
    - 报告状态不变（待确认/已确认/已推送均可刷新文字）
    - 渲染失败不会写库，保证"文字与 docx 一致"
    """
    await _get_or_404(db, report_id)
    override = payload.ai_config.to_db() if payload and payload.ai_config else None
    try:
        result = await report_service.regenerate_insight(
            report_id, slot, config_override=override, actor_id=current_user.id
        )
    except ReportError as exc:
        raise _err(exc) from None
    return InsightSlotResult(
        # 后端可能把旧槽位 key 迁移到当前绑定名（兼容升级前的报告），
        # 以返回值里的 slot 为准，前端据此更新对应的那一段
        slot=result.get("slot", slot),
        text=result["text"],
        unverified_numbers=result["unverified_numbers"],
        sample_leak=result["sample_leak"],
        degraded=result["degraded"],
        retried=result["retried"],
        model=result["model"],
    )
