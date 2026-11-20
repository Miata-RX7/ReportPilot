"""效率价值计算（2026-11-20）。

回答「这套系统到底省了多少时间」：按**任务**逐行，对比
「人工写一份报告要多久」与「在平台上配置 + 让它自动跑」。

耗时口径（与用户确认）：

============================  ==========================  ==========================
项                             人工（每次）                平台（一次性配置）
============================  ==========================  ==========================
数值类型指标                   1 分钟 / 个                  2 分钟 / 个
曲线图类型指标                 2 分钟 / 个                  4 分钟 / 个
AI 摘要                        5 分钟 / 个（按占位符）      2 分钟 / 个（按占位符）
确认阶段表格统计               15 分钟（条件计入）          ——（平台侧见下）
============================  ==========================  ==========================

- **人工基线是「每次」的**：手工写报告时每个变量都要重新取一遍数。
- **平台配置是「一次性」的**：配置一次，之后每次执行自动跑。
- **确认阶段（条件计入）**：只有该报告确实**上传了 Excel 并执行了 AI 生成
  摘要**才算（同一份 Excel 生成多次摘要只算 1 次）。此时：
  - 人工侧 +15 分钟/次
  - 平台侧 +3 分钟/次（上传 Excel 1 分钟 + AI 生成摘要 2 分钟）
- **执行次数**：只算成功的（``pushed`` / ``confirmed_no_push``）。
- **指标类型映射**（用户只给了「数值 / 曲线图」两类，其余按同类归并）：
  - 图形类（``curve`` / ``pie``）-> 曲线图口径
  - 其余（``scalar`` / ``text`` / ``time`` / ``calculated``）-> 数值口径

累计节省 = 执行次数 × 人工每次 − 平台一次性 − 执行次数 × 平台每次
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metric import MetricDefinition
from app.models.report import Report, ReportSummary
from app.models.task import ReportTask
from app.models.template import TemplateBinding, TemplateVersion, WordTemplate

logger = logging.getLogger(__name__)

#: 人工：数值类型指标，每个 1 分钟
MANUAL_SCALAR_MINUTES = 1
#: 人工：曲线图类型指标，每个 2 分钟
MANUAL_CURVE_MINUTES = 2
#: 人工：AI 摘要，每个占位符 5 分钟
MANUAL_INSIGHT_MINUTES = 5
#: 人工：确认阶段表格统计（条件计入），15 分钟
MANUAL_EXCEL_SUMMARY_MINUTES = 15

#: 平台：配置数值类型指标，每个 2 分钟（一次性）
PLATFORM_SCALAR_MINUTES = 2
#: 平台：配置曲线图类型指标，每个 4 分钟（一次性）
PLATFORM_CURVE_MINUTES = 4
#: 平台：配置 AI 摘要，每个占位符 2 分钟（一次性）
PLATFORM_INSIGHT_MINUTES = 2
#: 平台：确认阶段「上传 Excel + AI 生成摘要」，每次 3 分钟
#: （上传 1 分钟 + 生成 2 分钟）
PLATFORM_EXCEL_SUMMARY_MINUTES = 3

#: 图形类指标（曲线图口径）
_GRAPHIC_TYPES = {"curve", "pie"}

#: 计入「执行次数」的终态（只算成功的）
SUCCESS_STATUSES = ("pushed", "confirmed_no_push")


@dataclass(frozen=True)
class TaskComposition:
    """一个任务的构成（取自模板**当前版本**的绑定）。"""

    scalar_count: int = 0
    curve_count: int = 0
    insight_count: int = 0

    @property
    def total_metrics(self) -> int:
        return self.scalar_count + self.curve_count


@dataclass(frozen=True)
class TaskEfficiency:
    """单个任务的效率账。"""

    task_id: int
    task_name: str
    template_id: int | None
    template_name: str | None
    owner_id: int | None
    owner_name: str | None
    composition: TaskComposition
    has_excel_summary: bool
    run_count: int

    # 每次执行的人工耗时（分钟）
    manual_per_run: int
    # 平台的一次性配置投入（分钟）
    platform_setup: int
    # 平台每次执行的人工参与（分钟）—— 仅确认阶段
    platform_per_run: int
    # 每次执行净节省（分钟）
    saved_per_run: int
    # 累计净节省（分钟）= run_count × manual_per_run − platform_setup − run_count × platform_per_run
    total_saved: int
    # 盈亏平衡：执行几次才回本（= ceil(platform_setup / saved_per_run)）；saved_per_run<=0 时 None
    breakeven_runs: int | None
    is_mine: bool


def _tag_of(metric_type: str | None) -> str:
    """指标类型 -> 口径标签（graphic / scalar）。"""
    return "graphic" if (metric_type or "scalar") in _GRAPHIC_TYPES else "scalar"


async def _composition_of(db: AsyncSession, template_id: int | None) -> TaskComposition:
    """统计模板**当前版本**的绑定构成。

    - ``binding_type='metric'`` -> 按被绑定指标的 metric_type 归类
    - ``binding_type='insight'`` -> AI 摘要占位符（一个占位符 = 一段摘要）
    """
    if template_id is None:
        return TaskComposition()
    tpl = await db.get(WordTemplate, template_id)
    if tpl is None or tpl.current_version_id is None:
        return TaskComposition()
    rows = (
        await db.execute(
            select(TemplateBinding.binding_type, TemplateBinding.binding_ref_id).where(
                TemplateBinding.version_id == tpl.current_version_id
            )
        )
    ).all()
    if not rows:
        return TaskComposition()

    metric_ids = {
        int(bid)
        for btype, bid in rows
        if btype == "metric" and bid is not None
    }
    insight_count = sum(1 for btype, _bid in rows if btype == "insight")

    types: dict[int, str | None] = {}
    if metric_ids:
        type_rows = (
            await db.execute(
                select(MetricDefinition.id, MetricDefinition.metric_type).where(
                    MetricDefinition.id.in_(metric_ids)
                )
            )
        ).all()
        types = {int(mid): mt for mid, mt in type_rows}

    scalar = curve = 0
    for btype, bid in rows:
        if btype != "metric" or bid is None:
            continue
        if _tag_of(types.get(int(bid))) == "graphic":
            curve += 1
        else:
            scalar += 1
    return TaskComposition(
        scalar_count=scalar, curve_count=curve, insight_count=insight_count
    )


async def _has_excel_summary(db: AsyncSession, task_id: int) -> bool:
    """该任务的历史报告里，是否**确实上传了 Excel 并执行了 AI 生成摘要**。

    ``report_summaries.file_id`` 指向上传的 Excel；``file_id IS NOT NULL``
    即「这份摘要是从上传的 Excel 生成的」。
    """
    cnt = await db.scalar(
        select(func.count())
        .select_from(ReportSummary)
        .join(Report, Report.id == ReportSummary.report_id)
        .where(
            Report.task_id == task_id,
            ReportSummary.file_id.is_not(None),
        )
    )
    return bool(cnt)


async def _run_count(db: AsyncSession, task_id: int) -> int:
    """执行次数（只算成功的终态）。"""
    cnt = await db.scalar(
        select(func.count())
        .select_from(Report)
        .where(Report.task_id == task_id, Report.status.in_(SUCCESS_STATUSES))
    )
    return int(cnt or 0)


def _compose_numbers(
    comp: TaskComposition, has_excel: bool
) -> tuple[int, int, int]:
    """(人工每次, 平台一次性, 平台每次)，单位分钟。"""
    manual = (
        comp.scalar_count * MANUAL_SCALAR_MINUTES
        + comp.curve_count * MANUAL_CURVE_MINUTES
        + comp.insight_count * MANUAL_INSIGHT_MINUTES
    )
    platform_setup = (
        comp.scalar_count * PLATFORM_SCALAR_MINUTES
        + comp.curve_count * PLATFORM_CURVE_MINUTES
        + comp.insight_count * PLATFORM_INSIGHT_MINUTES
    )
    platform_per_run = PLATFORM_EXCEL_SUMMARY_MINUTES if has_excel else 0
    if has_excel:
        manual += MANUAL_EXCEL_SUMMARY_MINUTES
    return manual, platform_setup, platform_per_run


async def compute_task_efficiency(
    db: AsyncSession, task: ReportTask, *, current_user_id: int | None
) -> TaskEfficiency:
    """算单个任务的效率账。"""
    comp = await _composition_of(db, task.template_id)
    has_excel = await _has_excel_summary(db, task.id)
    runs = await _run_count(db, task.id)
    manual, setup, per_run = _compose_numbers(comp, has_excel)

    saved_per_run = manual - per_run
    total_saved = runs * manual - setup - runs * per_run
    breakeven: int | None = None
    if saved_per_run > 0 and setup > 0:
        breakeven = -(-setup // saved_per_run)  # ceil

    tpl = await db.get(WordTemplate, task.template_id) if task.template_id else None
    owner_name = task.created_by_name if hasattr(task, "created_by_name") else None

    return TaskEfficiency(
        task_id=task.id,
        task_name=task.name,
        template_id=task.template_id,
        template_name=tpl.name if tpl is not None else None,
        owner_id=task.created_by,
        owner_name=owner_name,
        composition=comp,
        has_excel_summary=has_excel,
        run_count=runs,
        manual_per_run=manual,
        platform_setup=setup,
        platform_per_run=per_run,
        saved_per_run=saved_per_run,
        total_saved=total_saved,
        breakeven_runs=breakeven,
        is_mine=task.created_by == current_user_id,
    )


async def compute_all_tasks(
    db: AsyncSession, *, current_user_id: int | None
) -> list[TaskEfficiency]:
    """全部任务的效率账（按创建时间倒序）。"""
    tasks = list(
        (
            await db.scalars(
                select(ReportTask).order_by(ReportTask.created_at.desc())
            )
        ).all()
    )
    out: list[TaskEfficiency] = []
    for t in tasks:
        try:
            out.append(
                await compute_task_efficiency(
                    db, t, current_user_id=current_user_id
                )
            )
        except Exception:  # 单个任务算不出来不该拖垮整个页面
            logger.exception("计算任务效率失败 task=%d", t.id)
    return out


def summarize(items: list[TaskEfficiency]) -> dict[str, Any]:
    """全局汇总。"""
    return {
        "task_count": len(items),
        "run_count": sum(i.run_count for i in items),
        "total_manual_minutes": sum(i.run_count * i.manual_per_run for i in items),
        "total_platform_minutes": sum(
            i.platform_setup + i.run_count * i.platform_per_run for i in items
        ),
        "total_saved_minutes": sum(i.total_saved for i in items),
    }
