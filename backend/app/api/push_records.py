"""推送记录查询：报告邮件的每次发送尝试（成功/失败都记）。

覆盖三类发送：报告推送（push / retry_push）、确认邮件（confirmation）、
24h 未确认催办提醒（reminder）—— 凡是真实发出的邮件都在这里有痕。
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.report import PushRecord, Report
from app.models.template import WordTemplate
from app.models.user import User
from app.schemas.report import PushRecordList, PushRecordRead

logger = logging.getLogger(__name__)

router = APIRouter()

# 一次返回上限（推送记录量级远小于报告，不做分页）
MAX_LIMIT = 200


@router.get("", response_model=PushRecordList)
async def list_push_records(
    report_id: int | None = Query(default=None, description="按报告过滤"),
    status: Literal["success", "failed"] | None = Query(default=None),
    action: Literal["push", "retry_push", "confirmation", "reminder"] | None = Query(
        default=None
    ),
    keyword: str | None = Query(default=None, description="报告 id（精确）或周期（模糊）"),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0, description="分页偏移；不传行为不变"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> PushRecordList:
    """推送记录列表（按时间倒序）。

    记录的是**发送事实**：发给谁、几个、附件名、耗时、成功/失败、失败原因。
    与报告审核记录（report_audits）互补：后者记"谁点了推送"。
    """
    filters = []
    if report_id is not None:
        filters.append(PushRecord.report_id == report_id)
    if status is not None:
        filters.append(PushRecord.status == status)
    if action is not None:
        filters.append(PushRecord.action == action)
    if keyword:
        kw = keyword.strip()
        like = f"%{kw}%"
        # 纯数字按报告 id 精确匹配，否则只按周期模糊匹配
        # （不用 cast(id AS text) 模糊匹配：SQLite 与 PG 行为不一致）
        cond = Report.period.ilike(like)
        if kw.isdigit():
            cond = or_(cond, Report.id == int(kw))
        filters.append(cond)

    stmt = select(PushRecord, Report).join(Report, Report.id == PushRecord.report_id)
    count_stmt = select(func.count(PushRecord.id))
    for f in filters:
        stmt = stmt.where(f)
        count_stmt = count_stmt.where(f)

    total = int(await db.scalar(count_stmt) or 0)
    rows = (
        await db.execute(
            stmt.order_by(PushRecord.created_at.desc(), PushRecord.id.desc()).limit(limit).offset(offset)
        )
    ).all()

    # 报告名（模板名 + 周期）直接给出，避免前端逐条反查
    tpl_ids = {rep.template_id for _, rep in rows}
    tpl_names: dict[int, str] = {}
    if tpl_ids:
        for t in (
            await db.scalars(select(WordTemplate).where(WordTemplate.id.in_(tpl_ids)))
        ).all():
            tpl_names[t.id] = t.name

    items = [
        PushRecordRead(
            id=rec.id,
            report_id=rec.report_id,
            report_name=(
                f"{tpl_names.get(rep.template_id, f'模板#{rep.template_id}')}_{rep.period}"
            ),
            attempt=rec.attempt,
            action=rec.action,
            status=rec.status,
            recipients=list(rec.recipients or []),
            recipient_count=rec.recipient_count,
            attachment_name=rec.attachment_name,
            duration_ms=rec.duration_ms,
            error=rec.error,
            actor_id=rec.actor_id,
            created_at=rec.created_at,
        )
        for rec, rep in rows
    ]
    logger.info("推送记录查询 total=%d returned=%d", total, len(items))
    return PushRecordList(total=total, items=items)
