"""pie 适用性判定。

用途：用户在配置 pie 指标前，先判断某个 item 是否适合做 UP/DOWN 占比分析
（例如 ICMP PING 类 0/1 状态量），并给出建议的 up_values / down_values。

判定口径（与 calc_engine 的 pie 计算保持一致）：
- distinct 值统计用 **value_max**：0/1 型 item 在某小时内既有 0 又有 1 时，
  value_avg 会得到 0.5 被误判成 3 个值；value_max 视该小时为出现过非零。
- 拉取窗口 7 天（足够判断 distinct 值，且比 30 天快很多）。
- 5 秒超时：超时返回 suitable=unknown，提示人工确认（不硬失败）。
"""

import asyncio
import logging
import re
import time
from typing import Any

from app.schemas.metric import PieSuitability
from app.services.adapters.base import DataSourceAdapter

logger = logging.getLogger(__name__)

# 拉取窗口：7 天
WINDOW_DAYS = 7
# 超时：5 秒
TIMEOUT_SECONDS = 5.0
# distinct 值上限：超过即判定"状态过多"
MAX_DISTINCT = 5
# 数值型监控项（0=浮点, 3=无符号整数）
NUMERIC_VALUE_TYPES = (0, 3)

# 命中这些关键词即认为是状态型（UP/DOWN/PING/ONLINE）
UP_DOWN_KEYWORDS = ("up", "down", "ping", "online", "offline", "unreachable", "reachable")
# key 命中已知模式 -> 置信度提升一级
KNOWN_STATE_KEYS = ("icmpping", "icmp", "ping", "status", "state")


def _format_value(v: float) -> str:
    """紧凑数值文本：整数去小数尾巴，浮点最多 4 位（避免 0.1+0.2 式长尾污染 UI）。"""
    f = float(v)
    if f.is_integer():
        return str(int(f))
    return f"{f:.4f}".rstrip("0").rstrip(".")


def _format_distinct(values: list[str]) -> str:
    """distinct 列表展示：最多列 5 个，超出用省略号。"""
    shown = values[:5]
    tail = " …" if len(values) > 5 else ""
    return ", ".join(shown) + tail


def _classify_distinct(
    distinct: list[str]
) -> tuple[bool, str, list[str], list[str]]:
    """按 distinct 值数量给出 suitable / confidence / 建议 up/down。

    返回 (suitable, confidence, suggested_up, suggested_down)。
    """
    n = len(distinct)
    if n == 0:
        return False, "low", [], []
    if n == 1:
        # 只有一个值：恒定状态，做占比没有意义
        return False, "low", [], []
    if n == 2:
        # 小值 DOWN、大值 UP
        try:
            ordered = sorted(distinct, key=lambda x: float(x))
        except ValueError:
            ordered = sorted(distinct)
        return True, "high", [ordered[-1]], [ordered[0]]
    if n <= MAX_DISTINCT:
        # 3~5 个值：让用户自己确认
        return True, "medium", [], []
    return False, "low", [], []


def _bump(confidence: str) -> str:
    """置信度提升一级：low -> medium -> high。"""
    if confidence == "low":
        return "medium"
    if confidence == "medium":
        return "high"
    return "high"


def _valuemap_suggestion(valuemap: dict[str, str]) -> tuple[list[str], list[str]]:
    """从 Zabbix valuemap 推断 UP / DOWN 取值（文本含关键词即可）。"""
    up: list[str] = []
    down: list[str] = []
    for raw, text in valuemap.items():
        t = str(text).lower()
        if any(k in t for k in ("up", "online", "reachable", "1")) and not any(
            k in t for k in ("down", "offline", "unreachable")
        ):
            up.append(raw)
        elif any(k in t for k in ("down", "offline", "unreachable", "0")):
            down.append(raw)
    return sorted(up), sorted(down)


async def analyze_pie_suitability(
    adapter: DataSourceAdapter,
    item_id: int,
) -> PieSuitability:
    """判定 item 是否适合 pie 占比分析。"""
    meta = await adapter.get_item_meta(item_id)
    if meta is None:
        return PieSuitability(
            suitable=False,
            confidence="low",
            reason=f"监控项 {item_id} 不存在或不在可访问范围内",
            distinct_values=[],
            suggested_config=None,
            has_valuemap=False,
            valuemap={},
        )
    valuemap = {str(k): str(v) for k, v in (meta.get("valuemap") or {}).items()}
    value_type = int(meta.get("value_type", -1))
    name = meta.get("name", "")
    key = meta.get("key", "")

    # 1) 非数值型直接不适合
    if value_type not in NUMERIC_VALUE_TYPES:
        return PieSuitability(
            suitable=False,
            confidence="low",
            reason=f"非数值型监控项（value_type={value_type}），无法做占比分析",
            distinct_values=[],
            suggested_config=None,
            has_valuemap=bool(valuemap),
            valuemap=valuemap,
        )

    # 2) 7 天窗口 + 5 秒超时
    now = int(time.time())
    trend = await _fetch_trend_with_timeout(adapter, item_id, now)
    if trend is None:
        return PieSuitability(
            suitable="unknown",
            confidence="unknown",
            reason="数据量过大或数据源响应超时，请人工确认",
            distinct_values=[],
            suggested_config=None,
            has_valuemap=bool(valuemap),
            valuemap=valuemap,
        )

    distinct = sorted({_format_value(r["value_max"]) for r in trend})
    suitable, confidence, sug_up, sug_down = _classify_distinct(distinct)

    # 3) valuemap 关键词优先
    vm_up, vm_down = _valuemap_suggestion(valuemap)
    has_vmap = bool(valuemap)
    if vm_up and vm_down:
        sug_up, sug_down = vm_up, vm_down
        if not suitable and confidence == "low":
            # valuemap 明确是 UP/DOWN 语义，即使 distinct 多也认为适合
            suitable, confidence = True, "medium"
    if has_vmap and suitable:
        confidence = _bump(confidence)

    # 4) 已知 key 模式提升置信度
    boosted = False
    if suitable and any(k in key.lower() for k in KNOWN_STATE_KEYS):
        confidence = _bump(confidence)
        boosted = True

    reason = _build_reason(suitable, confidence, distinct, valuemap, key, boosted)
    suggested = None
    if suitable and (sug_up or vm_up) and (sug_down or vm_down):
        suggested = {"up_values": sug_up or vm_up, "down_values": sug_down or vm_down}

    return PieSuitability(
        suitable=suitable,
        confidence=confidence,
        reason=reason,
        distinct_values=distinct,
        suggested_config=suggested,
        has_valuemap=has_vmap,
        valuemap=valuemap,
    )


def _build_reason(
    suitable: bool | str,
    confidence: str,
    distinct: list[str],
    valuemap: dict[str, str],
    key: str,
    boosted: bool,
) -> str:
    """生成面向用户的判定说明（如实描述 distinct 数量与置信度来源）。"""
    conf_cn = {"high": "高", "medium": "中", "low": "低", "unknown": "未知"}.get(
        confidence, confidence
    )
    boost_note = "（key 命中已知状态模式，已提升置信度）" if boosted else ""
    dist_txt = _format_distinct(distinct)

    if suitable == "unknown":
        return "数据量过大或数据源响应超时，请人工确认"
    if not suitable:
        if not distinct:
            return "窗口内无数据，不适合做占比分析"
        if len(distinct) == 1:
            return f"历史数据只有单一值 {dist_txt}，恒定状态无占比意义"
        if len(distinct) > MAX_DISTINCT:
            return f"状态过多（{len(distinct)} 个值：{dist_txt}），不适合做占比分析"
        return "不适合做占比分析"

    if valuemap:
        return f"item 配置了 value map（{_format_distinct(list(valuemap))}），可按其语义归类 UP/DOWN（{conf_cn}置信度）"
    if len(distinct) == 2:
        return f"历史数据只有 2 个值 [{dist_txt}]，符合 UP/DOWN 特征（{conf_cn}置信度）"
    return (
        f"历史数据有 {len(distinct)} 个值 [{dist_txt}]，请人工确认 UP/DOWN 映射"
        f"（{conf_cn}置信度）{boost_note}"
    )


async def _fetch_trend_with_timeout(
    adapter: DataSourceAdapter, item_id: int, now: int
) -> list[dict[str, Any]] | None:
    """在 5 秒超时内拉取 7 天 trend；超时返回 None。"""
    start = now - WINDOW_DAYS * 86400
    try:
        return await asyncio.wait_for(
            adapter.fetch_trend_data([item_id], start, now), timeout=TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning("pie 适合性判定超时 item=%s window=%ss", item_id, WINDOW_DAYS)
        return None
