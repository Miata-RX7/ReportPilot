"""曲线点集的统计摘要 —— Word 图内标注与 AI 摘要的**唯一取数口径**。

两条红线决定了这些数必须是后端算好的：

1. 「AI 不算数」：一条曲线可能有几万个采样点，全塞进 prompt 等于让模型自己找
   峰值、自己求平均（既要它算、又要它别算，必然出错）。摘要在这里算好，模型只能引用。
2. 口径唯一：同一张图的标注（`chart_renderer`）与 AI 摘要里引用的峰值/均值必须是
   同一套算法，否则同一张图两个数 —— 历史上真的对不上过。

本模块只做聚合，**不做单位换算**：换算由调用方把 `MetricFact.display_factor`
作为 `factor` 传进来（除以 factor 即展示口径，与 `unit_scale.ResolvedScale.apply`
一致）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SeriesStats:
    """一条曲线的统计摘要；数值均已按 factor 缩放到展示口径。

    无有效点时为 ``None``（不是 0）：报告里"没有数据"与"值为 0"是两件事。
    """

    count: int
    min: float | None
    max: float | None
    avg: float | None
    last: float | None


EMPTY_STATS = SeriesStats(count=0, min=None, max=None, avg=None, last=None)


def stats_of(points: Iterable[Any] | None, *, factor: float = 1.0) -> SeriesStats:
    """聚合 ``[[unix_ts, value], ...]``，返回 min/max/avg/last 与有效点数。

    - ``factor``：展示缩放除数（``display_factor``；None / 1 表示不换算）。
      非法值（0 / 负数）按不换算处理，避免脏数据把值算成 inf。
    - 缺值点（``None``）与形态不合法的点直接跳过，不让脏数据打断报告生成；
      与 `chart_renderer` 构造点集时的过滤条件保持一致。
    """
    divisor = factor if isinstance(factor, (int, float)) and factor > 0 else 1.0
    values: list[float] = []
    for p in points or []:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        v = p[1]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        values.append(float(v) / divisor)
    if not values:
        return EMPTY_STATS
    return SeriesStats(
        count=len(values),
        min=min(values),
        max=max(values),
        avg=sum(values) / len(values),
        last=values[-1],
    )