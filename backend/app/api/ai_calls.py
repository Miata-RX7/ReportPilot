"""AI 调用留痕查询：把「每一次请求 AI 的具体参数与 prompt」暴露给界面。

只读接口；写入在 `services/ai_trace`（由 `ai_insight.generate_summary` 这一
唯一出口触发）。表中不含 api_key，`base_url` 已去 userinfo。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.ai_call import AiCallLog
from app.models.user import User
from app.schemas.ai_call import AiCallDetail, AiCallItem, AiCallList

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=AiCallList)
async def list_ai_calls(
    report_id: int | None = Query(None, description="按报告过滤"),
    template_id: int | None = Query(None, description="按模板过滤（试跑历史）"),
    slot: str | None = Query(None, max_length=128, description="按摘要槽位过滤"),
    kind: str | None = Query(None, pattern="^(generate|regenerate|preview)$"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AiCallList:
    """倒序列出 AI 调用记录（默认最近 20 条，不含请求 / 响应正文）。"""
    filters = []
    if report_id is not None:
        filters.append(AiCallLog.report_id == report_id)
    if template_id is not None:
        filters.append(AiCallLog.template_id == template_id)
    if slot:
        filters.append(AiCallLog.slot == slot)
    if kind:
        filters.append(AiCallLog.kind == kind)

    total = await db.scalar(
        select(func.count()).select_from(AiCallLog).where(*filters)
    )
    rows = (
        await db.scalars(
            select(AiCallLog)
            .where(*filters)
            .order_by(AiCallLog.id.desc())
            .limit(limit)
        )
    ).all()
    return AiCallList(
        total=total or 0, items=[AiCallItem.model_validate(row) for row in rows]
    )


@router.get("/{log_id}", response_model=AiCallDetail)
async def get_ai_call(
    log_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AiCallDetail:
    """单条详情：完整 messages、模型参数、响应原文与护栏重试的第二次请求。"""
    log = await db.get(AiCallLog, log_id)
    if log is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "AI 调用记录不存在")
    return AiCallDetail.model_validate(log)