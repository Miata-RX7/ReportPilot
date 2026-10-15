"""单位换算：纯比例的「展示派生层」。

事实层（``value`` / ``baseline_value`` / ``change_pct``）永远是原始口径，本模块只回答
「怎么展示」：按指标级 ``unit_config`` 选一个比例因子，算出展示值与展示单位。

**为什么放展示层而不是取数层**：``scale(agg(x)) ≡ agg(scale(x))``（纯比例可交换），且
``change_pct`` 是比值、对缩放不敏感 —— 两者数学等价。放展示层可以完全不动「两阶段聚合 +
日历月平移基线」两条红线，也不需要重算历史报告。

**为什么因子必须由本期值决定、基线复用同一个因子**：本期选 M 档、基线选 K 档会让环比失真。
曲线（series）取全部点的最大绝对值统一选档。

语义必须与 ``frontend/src/utils/unit.ts`` 一致（两侧各有单测锁定，同 ``time.ts`` 的先例）。

配置形态（``metric_definitions.unit_config``，可空）：:

    {"mode": "none"}                                        # 默认，行为与升级前完全一致
    {"mode": "auto", "base": 1000, "suffix_unit": "bps"}    # 262468.9 -> 262.47 Kbps
    {"mode": "fixed", "divisor": 1000, "display_unit": "Kbps"}
    {"mode": "auto", "base": 1024, "suffix_unit": "B", "decimals": 3}

``suffix_unit`` 留空时回退指标原始 ``unit``（"bps" + auto -> Kbps/Mbps），``precisions``
留空时回退指标 ``display_precision``、再回退 2 位。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# 自动选档前缀表：index = 进制的幂次
PREFIXES_1000: tuple[str, ...] = ("", "K", "M", "G", "T", "P", "E")
PREFIXES_1024: tuple[str, ...] = ("", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei")
VALID_BASES: tuple[int, ...] = (1000, 1024)
# 换算开启但指标未配 display_precision 时的展示小数位（262.4689041666667 -> 262.47）
DEFAULT_DISPLAY_DECIMALS = 2
MAX_AUTO_ORDER = len(PREFIXES_1000) - 1


@dataclass(frozen=True)
class ResolvedScale:
    """一次解析的结果：缩放因子 + 展示单位 + 展示精度。"""

    mode: str
    factor: float
    unit: str
    precision: int

    def apply(self, value: float | None) -> float | None:
        """把原始值缩放到展示口径（None 透传）。"""
        if value is None:
            return None
        return value / self.factor

    def describe(self) -> str:
        """追加到 formula 上的可读口径，如「÷1000 → Kbps」。"""
        factor_text = f"÷{self.factor:g}"
        if not self.unit:
            return factor_text
        return f"{factor_text} → {self.unit}"


@dataclass(frozen=True)
class UnitScale:
    """解析后的 ``unit_config``；非法或缺省一律退化为 mode=none（完全等价升级前行为）。"""

    mode: str = "none"
    base: int = 1000
    suffix_unit: str = ""
    divisor: float = 1.0
    display_unit: str = ""
    decimals: int | None = None

    @property
    def enabled(self) -> bool:
        return self.mode in ("auto", "fixed")

    @classmethod
    def parse(cls, raw: Any) -> "UnitScale":
        """宽容解析：非法/缺省配置退化为「不换算」，不让脏数据打断报告生成。

        写路径的严格校验在 ``schemas.metric.UnitScaleConfig``；这里只服务读路径
        （存量数据、手工改过 DB 的配置），失败时告警而非抛错。
        """
        if not isinstance(raw, dict):
            return cls()
        mode = raw.get("mode")
        if mode not in ("auto", "fixed"):
            return cls()
        decimals = _parse_decimals(raw.get("decimals"))
        if mode == "auto":
            base = raw.get("base")
            if base not in VALID_BASES:
                if base is not None:
                    logger.warning("unit_config 进制非法，已回退 1000: %r", base)
                base = 1000
            return cls(
                mode="auto",
                base=int(base),
                suffix_unit=str(raw.get("suffix_unit") or "").strip(),
                decimals=decimals,
            )
        divisor = _parse_divisor(raw.get("divisor"))
        if divisor is None:
            logger.warning(
                "unit_config 固定除数非法，已按不换算处理: %r", raw.get("divisor")
            )
            return cls()
        return cls(
            mode="fixed",
            divisor=divisor,
            display_unit=str(raw.get("display_unit") or "").strip(),
            decimals=decimals,
        )

    def resolve(
        self,
        *,
        magnitude: float,
        base_unit: str = "",
        precision: int | None = None,
    ) -> ResolvedScale | None:
        """按 ``magnitude``（本期值 / 曲线全点最大绝对值）解析缩放口径。

        返回 None 表示「不换算」，调用方此时必须继续使用原始值与原始单位。
        """
        if not self.enabled:
            return None
        if self.mode == "fixed":
            return ResolvedScale(
                mode="fixed",
                factor=self.divisor,
                unit=self.display_unit or base_unit,
                precision=_display_precision(self.decimals, precision),
            )
        order = auto_order(magnitude, self.base)
        prefix = (PREFIXES_1000 if self.base == 1000 else PREFIXES_1024)[order]
        # 后缀优先用配置里的（bps -> Kbps），缺省回退指标原始单位
        suffix = self.suffix_unit or base_unit
        return ResolvedScale(
            mode="auto",
            factor=float(self.base**order),
            unit=f"{prefix}{suffix}",
            precision=_display_precision(self.decimals, precision),
        )


def auto_order(magnitude: float, base: int) -> int:
    """按量级选档：``|v| < base`` -> 0 档（原单位），否则逐级上升并封顶 E 档。

    用循环而非 ``log()``：``math.floor(math.log(1e9, 1000))`` 会因浮点误差得到 2。
    """
    m = abs(float(magnitude))
    if not math.isfinite(m):
        return 0
    order = 0
    limit = float(base)
    while order < MAX_AUTO_ORDER and m >= limit:
        order += 1
        limit *= base
    return order


def scale_value(value: float | None, scale: ResolvedScale | None) -> float | None:
    """按解析结果缩放；``scale`` 为 None 时原样返回，供消费端统一调用。"""
    if scale is None:
        return value
    return scale.apply(value)


def display_unit_of(scale: ResolvedScale | None, base_unit: str) -> str:
    """展示单位：未换算或换算后无单位时回退指标原始单位。"""
    if scale is None or not scale.unit:
        return base_unit
    return scale.unit


def _display_precision(decimals: int | None, metric_precision: int | None) -> int:
    """展示小数位：换算配置覆盖 > 指标 display_precision > 默认 2 位。"""
    if decimals is not None:
        return decimals
    if metric_precision is not None:
        return metric_precision
    return DEFAULT_DISPLAY_DECIMALS


def _parse_decimals(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return min(max(value, 0), 10)


def _parse_divisor(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value