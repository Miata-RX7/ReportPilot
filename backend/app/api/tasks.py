"""报告任务 API：CRUD + 启停 + 手动触发。"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ownership
from app.api.deps import get_current_user
from app.database import get_db
from app.models.report import Report
from app.models.task import ReportTask
from app.models.template import WordTemplate
from app.models.user import User
from app.schemas.task import (
    ReportTaskCreate,
    ReportTaskRead,
    ReportTaskRunResult,
    ReportTaskUpdate,
)
from app.services import task_service
from app.services.period_utils import PERIOD_PATTERN
from app.services.report_service import ReportError
from app.services.task_service import TaskError

logger = logging.getLogger(__name__)

router = APIRouter()


def _to_http(exc: Exception) -> int:
    from fastapi import HTTPException, status

    if isinstance(exc, TaskError):
        return status.HTTP_422_UNPROCESSABLE_ENTITY
    if isinstance(exc, ReportError):
        return status.HTTP_422_UNPROCESSABLE_ENTITY
    return status.HTTP_400_BAD_REQUEST


async def _creator_name(
    db: AsyncSession, created_by: int | None, current_user: User | None
) -> str | None:
    """创建人名字 —— **只有 admin 能拿到**。"""
    if created_by is None or current_user is None:
        return None
    if not ownership.is_admin(current_user):
        return None
    name = await db.scalar(select(User.username).where(User.id == created_by))
    return name


async def _read(
    db: AsyncSession, task: ReportTask, current_user: User | None = None
) -> ReportTaskRead:
    """组装读模型（附模板名、收件人数、待确认积压）。

    「创建人」名字只有 admin 能拿到 —— 在后端就掐掉，不靠前端隐藏。
    """
    tpl = await db.get(WordTemplate, task.template_id)
    pending = await db.scalar(
        select(func.count(Report.id)).where(
            Report.task_id == task.id,
            Report.status == "pending_confirmation",
        )
    )
    return ReportTaskRead(
        id=task.id,
        name=task.name,
        template_id=task.template_id,
        template_name=tpl.name if tpl else None,
        cron_expr=task.cron_expr,
        period_granularity=task.period_granularity,
        period_offset=task.period_offset,
        is_active=task.is_active,
        confirmer_email=task.confirmer_email,
        recipient_emails=list(task.recipient_emails or []),
        recipient_count=len(task.recipient_emails or []),
        created_by=task.created_by,
        created_by_name=(
            await _creator_name(db, task.created_by, current_user)
        ),
        created_at=task.created_at,
        updated_at=task.updated_at,
        last_run_at=task.last_run_at,
        next_run_at=task.next_run_at,
        last_skipped_at=task.last_skipped_at,
        last_error=task.last_error,
        pending_confirmation=int(pending or 0),
    )


@router.get("", response_model=list[ReportTaskRead])
async def list_tasks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ReportTaskRead]:
    """列表：**admin 见全部；普通用户只见自己创建的**（2026-11-11）。"""
    stmt = select(ReportTask).order_by(ReportTask.is_active.desc(), ReportTask.id)
    if not ownership.is_admin(current_user):
        stmt = stmt.where(ReportTask.created_by == current_user.id)
    tasks = list((await db.scalars(stmt)).all())
    return [await _read(db, t, current_user) for t in tasks]


@router.post("", response_model=ReportTaskRead, status_code=201)
async def create_task(
    payload: ReportTaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReportTaskRead:
    try:
        task = await task_service.create_task(
            db,
            name=payload.name,
            template_id=payload.template_id,
            cron_expr=payload.cron_expr,
            confirmer_email=payload.confirmer_email,
            recipient_emails=list(payload.recipient_emails),
            period_granularity=payload.period_granularity,
            period_offset=payload.period_offset,
            is_active=payload.is_active,
            actor_id=current_user.id,
        )
    except Exception as exc:
        from fastapi import HTTPException

        raise HTTPException(_to_http(exc), str(exc)) from None
    return await _read(db, task)


@router.get("/{task_id}", response_model=ReportTaskRead)
async def get_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReportTaskRead:
    task = await db.get(ReportTask, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    ownership.ensure_task_visible(task, current_user)
    return await _read(db, task)


@router.put("/{task_id}", response_model=ReportTaskRead)
async def update_task(
    task_id: int,
    payload: ReportTaskUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReportTaskRead:
    from fastapi import HTTPException, status

    task = await db.get(ReportTask, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    ownership.ensure_task_editable(task, current_user)
    try:
        task = await task_service.update_task(
            db, task, payload.model_dump(exclude_unset=True)
        )
    except Exception as exc:
        raise HTTPException(_to_http(exc), str(exc)) from None
    return await _read(db, task)


@router.post("/{task_id}/active", response_model=ReportTaskRead)
async def set_task_active(
    task_id: int,
    active: bool = Query(description="true=启用 false=停用"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReportTaskRead:
    from fastapi import HTTPException, status

    task = await db.get(ReportTask, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    ownership.ensure_task_editable(task, current_user)
    try:
        task = await task_service.set_active(db, task, active)
    except Exception as exc:
        raise HTTPException(_to_http(exc), str(exc)) from None
    return await _read(db, task)


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    from fastapi import HTTPException, status

    task = await db.get(ReportTask, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    ownership.ensure_task_editable(task, current_user)
    try:
        await task_service.delete_task(db, task)
    except Exception as exc:
        raise HTTPException(_to_http(exc), str(exc)) from None


@router.post("/{task_id}/run", response_model=ReportTaskRunResult)
async def run_task(
    task_id: int,
    period: str | None = Query(
        default=None,
        pattern=PERIOD_PATTERN,
        description="统计周期：YYYY-MM 或 YYYY-Www；缺省按任务配置的粒度/偏移派生",
    ),
    current_user: User = Depends(get_current_user),
) -> ReportTaskRunResult:
    """手动触发一次（**默认同样走确认流程**，用于真实演练）。

    立即返回；实际生成在线程池里执行（不阻塞请求）。
    """
    from fastapi import HTTPException, status

    from app.database import AsyncSessionLocal
    from app.services import scheduler

    async with AsyncSessionLocal() as db:
        task = await db.get(ReportTask, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    ownership.ensure_task_editable(task, current_user)
    try:
        report_id = await scheduler.run_task_instance(
            task_id, source="manual", period_override=period
        )
    except Exception as exc:
        logger.exception("手动触发失败 task=%d", task_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"触发失败: {exc}"
        ) from None
    if report_id is None:
        return ReportTaskRunResult(
            report_id=None,
            status="skipped",
            reason="任务已停用、已被其它实例认领，或该触发时刻已有实例",
        )
    return ReportTaskRunResult(report_id=report_id, status="created")
