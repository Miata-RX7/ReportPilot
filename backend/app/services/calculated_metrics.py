"""计算型指标的保存校验（2026-11-12）。

规则：
- 算式语法合法（services/expr_eval）
- 公式里用到的变量都已定义
- 变量引用的指标存在、未停用、是**基础 scalar**（不含计算型/曲线/饼图/文本）
- 变量引用的指标与计算型指标**同数据源**（calc_engine 是无状态的，只能用
  调用方传入的那一个 adapter 取数；不同源取不到）

保存时校验比运行时失败好得多 —— 用户填表时立刻能知道哪里不对。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metric import MetricDefinition
from app.services.expr_eval import ExpressionError, parse_expression

logger = logging.getLogger(__name__)

#: 内置变量名（与 calc_engine 一致）
BUILTIN_MINUTES = "minutes"


async def validate_calculated_config(
    db: AsyncSession,
    *,
    query_config: dict[str, Any] | None,
    data_source_id: int | None,
) -> None:
    """校验计算型指标的算式与变量定义。**不合法就抛 ValueError**。

    调用方（API 层）把它转成 422 返回给用户。
    """
    cfg = query_config or {}
    formula = str(cfg.get("formula") or "").strip()
    if not formula:
        raise ValueError("算式不能为空")
    variables = cfg.get("variables") or []

    try:
        parsed = parse_expression(formula)
    except ExpressionError as exc:
        raise ValueError(f"算式非法：{exc}") from None

    names = [str(v.get("name") or "").strip() for v in variables]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise ValueError(f"变量名重复：{', '.join(sorted(dup))}")

    defined: set[str] = set(names)
    if cfg.get("builtin_minutes", True):
        defined.add(BUILTIN_MINUTES)
    missing = [v for v in parsed.variables if v not in defined]
    if missing:
        raise ValueError(f"算式里的变量未定义：{', '.join(missing)}")

    unknown = [n for n in names if n not in parsed.variables]
    if unknown:
        logger.warning("计算型指标有未使用的变量：%s", unknown)

    if not variables:
        raise ValueError("至少要引用一个指标")

    ids = [int(v["metric_id"]) for v in variables if v.get("metric_id") is not None]
    if len(ids) != len(variables):
        raise ValueError("变量定义缺少 metric_id")
    rows = (
        await db.execute(
            select(MetricDefinition).where(MetricDefinition.id.in_(ids))
        )
    ).scalars().all()
    by_id = {m.id: m for m in rows}

    for var in variables:
        name = str(var.get("name") or "").strip()
        mid = int(var["metric_id"])
        dep = by_id.get(mid)
        if dep is None:
            raise ValueError(f"变量 {name} 引用的指标不存在（id={mid}）")
        if not dep.is_active:
            raise ValueError(f"变量 {name} 引用的指标已停用：{dep.name}")
        dep_type = dep.metric_type or "scalar"
        if dep_type == "calculated":
            raise ValueError(
                f"变量 {name} 引用的是计算型指标：{dep.name}（暂不支持嵌套）"
            )
        if dep_type != "scalar":
            raise ValueError(
                f"变量 {name} 引用的「{dep.name}」是{dep_type}类型，"
                "不能参与计算（只能引用数值类型指标）"
            )
        if dep.data_source_id != data_source_id:
            raise ValueError(
                f"变量 {name} 引用的「{dep.name}」与计算型指标的数据源不同，"
                "无法计算（请把计算型指标的数据源改成同一个）"
            )
