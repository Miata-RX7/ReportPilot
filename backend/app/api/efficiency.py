"""效率价值接口（2026-11-20）。

``GET /api/efficiency/value`` 返回：
- ``summary``：全局汇总（所有任务）
- ``tasks``：全部任务明细（每任务一行）
- ``mine``：当前用户自己创建的任务明细（普通用户的「我的任务」卡片）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.efficiency import EfficiencySummary, EfficiencyValue, TaskEfficiencyRead
from app.services import efficiency_service

router = APIRouter()


@router.get("/value", response_model=EfficiencyValue)
async def get_efficiency_value(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EfficiencyValue:
    """首页「效率价值」数据。

    所有登录用户都能看到**全部任务**的汇总与明细（价值体现是全局视角）；
    ``mine`` 额外给出当前用户名下任务的信息（普通用户的「我的任务」卡片）。
    """
    items = await efficiency_service.compute_all_tasks(
        db, current_user_id=current_user.id
    )
    summary = efficiency_service.summarize(items)
    mine = [i for i in items if i.is_mine]
    return EfficiencyValue(
        summary=EfficiencySummary(**summary),
        tasks=[_to_read(i) for i in items],
        mine=[_to_read(i) for i in mine],
        mine_summary=EfficiencySummary(**efficiency_service.summarize(mine)),
    )


def _to_read(i: efficiency_service.TaskEfficiency) -> TaskEfficiencyRead:
    return TaskEfficiencyRead(
        task_id=i.task_id,
        task_name=i.task_name,
        template_id=i.template_id,
        template_name=i.template_name,
        owner_id=i.owner_id,
        owner_name=i.owner_name,
        scalar_count=i.composition.scalar_count,
        curve_count=i.composition.curve_count,
        insight_count=i.composition.insight_count,
        total_metrics=i.composition.total_metrics,
        has_excel_summary=i.has_excel_summary,
        run_count=i.run_count,
        manual_per_run=i.manual_per_run,
        platform_setup=i.platform_setup,
        platform_per_run=i.platform_per_run,
        saved_per_run=i.saved_per_run,
        total_saved=i.total_saved,
        breakeven_runs=i.breakeven_runs,
        is_mine=i.is_mine,
    )
