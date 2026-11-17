"""计算型指标的依赖关系工具（2026-11-17）。

计算型指标（``metric_type == 'calculated'``）的算式里可以引用别的指标：

.. code-block:: json

    {"formula": "(100-a)/100*minutes",
     "variables": [{"name": "a", "metric_id": 50}]}

这里提供三件事：

1. :func:`dependents_of` —— **谁引用了某个指标**（删除它时会连带删除这些计算型指标）
2. :func:`broken_dependencies_of` —— 某个计算型指标的依赖里**哪些已失效**
3. :func:`variables_of` —— 取算式里的变量定义

依赖存在 ``query_config.variables`` 这个 JSON 里，用 SQL 过滤在 SQLite/PG
之间不好统一，所以统一在 Python 侧过滤（计算型指标数量很少，删除是低频操作）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metric import MetricDefinition


def variables_of(metric: MetricDefinition) -> list[dict[str, Any]]:
    """取一个计算型指标引用了哪些指标（变量定义列表）。"""
    if (metric.metric_type or "") != "calculated":
        return []
    cfg = metric.query_config or {}
    raw = cfg.get("variables") or []
    out: list[dict[str, Any]] = []
    for v in raw:
        if isinstance(v, dict) and v.get("metric_id") is not None:
            out.append(v)
    return out


async def dependents_of(
    db: AsyncSession, metric_id: int
) -> list[tuple[MetricDefinition, str]]:
    """引用了 ``metric_id`` 的**活跃**计算型指标。

    返回 ``[(计算型指标, 变量名), ...]``。
    """
    rows = (
        await db.scalars(
            select(MetricDefinition).where(
                MetricDefinition.metric_type == "calculated",
                MetricDefinition.is_active.is_(True),
            )
        )
    ).all()
    out: list[tuple[MetricDefinition, str]] = []
    for m in rows:
        for v in variables_of(m):
            if int(v["metric_id"]) == metric_id:
                out.append((m, str(v.get("name") or "")))
    return out


async def broken_dependencies_of(
    db: AsyncSession, metrics: list[MetricDefinition]
) -> dict[int, list[dict[str, Any]]]:
    """批量算出每个计算型指标的**失效依赖**。

    返回 ``{指标 id: [{"metric_id":..., "name":..., "var_name":...}, ...]}``。
    只包含已删除/停用的依赖；依赖都还在的指标不在结果里。
    """
    calc_metrics = [m for m in metrics if (m.metric_type or "") == "calculated"]
    if not calc_metrics:
        return {}

    dep_ids: set[int] = set()
    for m in calc_metrics:
        for v in variables_of(m):
            dep_ids.add(int(v["metric_id"]))
    if not dep_ids:
        return {}

    rows = (
        await db.scalars(
            select(MetricDefinition).where(MetricDefinition.id.in_(dep_ids))
        )
    ).all()
    by_id = {m.id: m for m in rows}

    out: dict[int, list[dict[str, Any]]] = {}
    for m in calc_metrics:
        broken: list[dict[str, Any]] = []
        for v in variables_of(m):
            mid = int(v["metric_id"])
            dep = by_id.get(mid)
            # 不存在 = 被硬删了；is_active=False = 被软删除（停用）
            if dep is None or not dep.is_active:
                broken.append(
                    {
                        "metric_id": mid,
                        "name": dep.name if dep is not None else f"（指标 {mid}）",
                        "var_name": str(v.get("name") or ""),
                    }
                )
        if broken:
            out[m.id] = broken
    return out
