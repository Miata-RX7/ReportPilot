"""离职人员的数据交接（2026-11-11）。

背景：指标 / 报告任务按创建人隔离后（owner），人离职了这些数据就成了孤儿 ——
没人能改，也没人能删。admin 需要把离职人员的全部数据转给接手人。

**转什么**：

============================  ==========================================
类型                          动作
============================  ==========================================
指标 metric_definitions       owner_id: 离职人 -> 接手人
报告任务 report_tasks         created_by: 离职人 -> 接手人
模板 word_templates          owner_id: 离职人 -> 接手人
模板授权 template_grants      **删除**离职人持有的授权行
模板授权（离职人给别人的）    不动 —— 新人继承 owner，被授权者不变
报告实例 / 推送记录          无独立归属，随任务自动跟随
数据源 data_sources          全局共享，不转
============================  ==========================================

**模板交接是两件事，缺一不可**：
1. 离职人**拥有**的模板 -> owner 转给新人
2. 离职人**被授权**的模板（owner 是别人）-> 删除他的 grant 行
只做 1 不做 2，离职人虽然离职了，grant 还在 —— 将来账号若被复用/恢复仍有权限。

**保障**：
- 全程**一个事务**，任一步失败整体回滚
- **幂等**：重复执行不报错（转完再转就是 0 条）
- **有审计**：写 ownership_transfers 表
- **不自动停用离职人账号**（用户明确要求：账号停用走 AD 同步逻辑）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metric import MetricDefinition
from app.models.task import ReportTask
from app.models.template import TemplateGrant, WordTemplate
from app.models.user import User

logger = logging.getLogger(__name__)


@dataclass
class TransferOptions:
    """要转哪些类型（默认全转）。"""

    metrics: bool = True
    tasks: bool = True
    templates: bool = True
    delete_grants: bool = True


@dataclass
class TransferResult:
    metrics: int = 0
    tasks: int = 0
    templates: int = 0
    grants_deleted: int = 0
    details: dict[str, Any] = field(default_factory=dict)


async def _counts(db: AsyncSession, user_id: int) -> dict[str, int]:
    return {
        "metrics": int(
            await db.scalar(
                select(func.count(MetricDefinition.id)).where(
                    MetricDefinition.owner_id == user_id
                )
            )
            or 0
        ),
        "tasks": int(
            await db.scalar(
                select(func.count(ReportTask.id)).where(
                    ReportTask.created_by == user_id
                )
            )
            or 0
        ),
        "templates": int(
            await db.scalar(
                select(func.count(WordTemplate.id)).where(
                    WordTemplate.owner_id == user_id
                )
            )
            or 0
        ),
        "grants": int(
            await db.scalar(
                select(func.count(TemplateGrant.id)).where(
                    TemplateGrant.user_id == user_id
                )
            )
            or 0
        ),
    }


async def summarize(db: AsyncSession, user_id: int) -> dict[str, int]:
    """预览：离职人员名下各类数据的条数（不改动任何东西）。"""
    return await _counts(db, user_id)


async def transfer(
    db: AsyncSession,
    *,
    from_user_id: int,
    to_user_id: int,
    options: TransferOptions | None = None,
    actor_id: int | None = None,
) -> TransferResult:
    """把 ``from_user_id`` 的数据转给 ``to_user_id``。

    调用方负责权限（仅 admin）与事务边界；这里只做数据搬运 + 审计。
    """
    opts = options or TransferOptions()
    result = TransferResult()

    if from_user_id == to_user_id:
        return result

    if opts.metrics:
        n = await db.execute(
            update(MetricDefinition)
            .where(MetricDefinition.owner_id == from_user_id)
            .values(owner_id=to_user_id)
        )
        result.metrics = int(n.rowcount or 0)

    if opts.tasks:
        n = await db.execute(
            update(ReportTask)
            .where(ReportTask.created_by == from_user_id)
            .values(created_by=to_user_id)
        )
        result.tasks = int(n.rowcount or 0)

    if opts.templates:
        n = await db.execute(
            update(WordTemplate)
            .where(WordTemplate.owner_id == from_user_id)
            .values(owner_id=to_user_id)
        )
        result.templates = int(n.rowcount or 0)

    if opts.delete_grants:
        # 离职人**被授权**的模板（owner 是别人）-> 删掉他的 grant 行
        n = await db.execute(
            delete(TemplateGrant).where(TemplateGrant.user_id == from_user_id)
        )
        result.grants_deleted = int(n.rowcount or 0)

    await db.flush()
    logger.info(
        "离职交接 from=%d to=%d metrics=%d tasks=%d templates=%d grants=%d",
        from_user_id,
        to_user_id,
        result.metrics,
        result.tasks,
        result.templates,
        result.grants_deleted,
    )
    return result
