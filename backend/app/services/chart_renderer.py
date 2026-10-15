"""指标图表渲染（matplotlib -> PNG 字节）。

纯函数：输入 MetricFact，输出 PNG 字节。不触 DB、不落盘（调用方直接喂给
docxtpl 的 InlineImage），因此没有临时文件清理与孤儿文件问题。

**为什么返回 bytes 而不是 PNG 路径**：docxtpl 的 InlineImage 接受文件路径或
file-like 对象（实测 BytesIO 可用），返回 bytes 让"是否落盘"成为调用方的
选择而非本模块的职责。

数字格式化的两套口径（有意不一致，勿"顺手统一"）：
- **Y 轴刻度**是辅助参考 -> 紧凑格式（1234567 -> 1.2M）。曲线原始值常是 bps
  量级，按 display_precision 会得到 5000000.00 这类长刻度，反而更难读。
- **数据标注**（最大/均值/末值）是精读值 -> 按 display_precision（默认 2 位）。
"""

import io
import logging
import time
from typing import Any

import matplotlib

matplotlib.use("Agg")  # slim 镜像无 GUI 库，必须在 pyplot 之前指定

import matplotlib.pyplot as plt
import numpy as np  # noqa: E402
from matplotlib import dates as mdates  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402

from app.schemas.metric import MetricFact
from app.services.series_stats import stats_of

logger = logging.getLogger(__name__)

DPI = 150
# 与前端 Design Token --chart-series-1..5 保持一致（容器内读不到前端 CSS）
SERIES_COLORS = ["#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6"]
# 候选中文字体，按优先级；首个可用者胜出
FONT_CANDIDATES = [
    "Noto Sans CJK SC",
    "WenQuanYi Micro Hei",
    "WenQuanYi Zen Hei",
    "Noto Sans CJK JP",
    "DejaVu Sans",
]
CURVE_SIZE_MM = (140.0, 70.0)
PIE_SIZE_MM = (100.0, 80.0)
# 统计标注最多列出的曲线数（超出用 "+N" 收尾，避免图例/标注糊成一片）
MAX_ANNOTATED_SERIES = 3
# 饼图语义色：UP=绿 / DOWN=红（与前端 Design Token 的 success/danger 对齐）
PIE_COLORS = ("#10B981", "#EF4444")
# 饼图默认标签（无法从 pie_config 推断语义时）
PIE_DEFAULT_LABELS = ("通", "断")

MM_PER_INCH = 25.4
SECONDS_PER_DAY = 86400.0

_FONT_READY = False


class ChartRenderError(Exception):
    """图表渲染失败。消息不含敏感信息。"""


def _ensure_font_configured() -> str:
    """配置中文字体（进程内只执行一次）。返回实际选中的字体名。

    字体缺失不阻塞报告生成：退回默认字体（中文可能显示为豆腐块），
    仅记 warning。
    """
    global _FONT_READY
    if _FONT_READY:
        return str(plt.rcParams["font.sans-serif"][0])
    chosen: str | None = None
    for name in FONT_CANDIDATES:
        try:
            font_manager.findfont(font_manager.FontProperties(family=name), fallback_to_default=False)
            chosen = name
            break
        except Exception:  # noqa: BLE001 - 逐个探测，缺失属正常情况
            continue
    if chosen is None:
        logger.warning(
            "未找到任何候选中文字体（%s），图表中文可能显示为方块；"
            "如需修复请在镜像中安装 fonts-wqy-microhei 或 fonts-noto-cjk",
            ", ".join(FONT_CANDIDATES),
        )
        chosen = FONT_CANDIDATES[-1]
        plt.rcParams["font.sans-serif"] = list(FONT_CANDIDATES)
    else:
        rest = [n for n in FONT_CANDIDATES if n != chosen]
        plt.rcParams["font.sans-serif"] = [chosen, *rest]
        logger.info("图表字体已配置 font=%s", chosen)
    # 负号在部分 CJK 字体里缺字形，不设会显示为方块
    plt.rcParams["axes.unicode_minus"] = False
    _FONT_READY = True
    return chosen


def _mm_to_px(size_mm: tuple[float, float], dpi: int) -> tuple[float, float]:
    """毫米 -> 英寸（matplotlib 画布单位），物理尺寸与 Word 插入宽度一致。"""
    return (size_mm[0] / MM_PER_INCH, size_mm[1] / MM_PER_INCH)


def compact_number(v: float) -> str:
    """Y 轴紧凑刻度：1234567 -> 1.23M。"""
    abs_v = abs(v)
    if abs_v >= 1e9:
        return f"{v / 1e9:.2f}G"
    if abs_v >= 1e6:
        return f"{v / 1e6:.2f}M"
    if abs_v >= 1e3:
        return f"{v / 1e3:.1f}K"
    return f"{v:g}"


def _format_value(v: float | None, precision: int | None) -> str:
    """数据标注（精读值）格式化。

    - precision 有值 -> 固定该位小数（与 format_fact_value 一致）
    - precision 为 None（指标未配 display_precision）-> 千分位 + 最多 2 位小数
      并去掉尾随 0。不用 %g 是因为它会把 4862609 写成 4.86261e+06（6 位有效
      数字，读起来像"精确到个位"），也不追加 4862609.00 这种无意义小数。
      与 format_fact_value 的 %g 略有差异是刻意的：图上要的是可读性。
    """
    if v is None:
        return "-"
    if precision is None:
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{v:.{max(0, min(6, precision))}f}"


def _annotate(
    ax: Any,
    series: list[dict[str, Any]],
    precision: int | None,
) -> None:
    """图内统计标注（最大/均值/末值，精读值口径）。

    统计走 `series_stats.stats_of`：AI 摘要里引用的峰值/均值是同一套算法，
    否则同一张图会出现两个数。这里传进来的点已在 `_render_curve` 里缩放到展示口径，
    因此 factor 用默认的 1。
    """
    lines: list[str] = []
    for s in series[:MAX_ANNOTATED_SERIES]:
        st = stats_of(s.get("points") or [])
        lines.append(
            f"{s.get('name', '')}: 最大 {_format_value(st.max, precision)} · "
            f"均值 {_format_value(st.avg, precision)} · 末值 {_format_value(st.last, precision)}"
        )
    if len(series) > MAX_ANNOTATED_SERIES:
        lines.append(f"另有 {len(series) - MAX_ANNOTATED_SERIES} 条曲线见图例")
    if not lines:
        return
    ax.text(
        0.99,
        0.97,
        "\n".join(lines),
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=5.5,
        color="#475569",
        linespacing=1.5,
        bbox={"facecolor": "#F8FAFC", "edgecolor": "#E2E8F0", "pad": 2},
    )


def _render_placeholder(
    message: str,
    size_mm: tuple[float, float],
    dpi: int,
) -> bytes:
    """无数据占位图：灰底 + 提示文案，不抛异常（不阻塞报告生成）。"""
    fig, ax = plt.subplots(figsize=_mm_to_px(size_mm, dpi))
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_facecolor("#F1F5F9")
    for spine in ax.spines.values():
        spine.set_color("#CBD5E1")
    ax.text(
        0.5,
        0.5,
        message,
        ha="center",
        va="center",
        fontsize=9,
        color="#64748B",
    )
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _render_curve(payload: dict[str, Any], fact: MetricFact, dpi: int) -> bytes:
    # 单位换算：点值与 Y 轴单位统一换算到展示口径（display_factor 为 None = 不换算）。
    # 必须在这里就缩放 points，而不是只缩放 ys —— 否则 _annotate 会继续标注原始值，
    # 图内标注与曲线数值不一致。
    factor = fact.display_factor or 1.0
    raw_series = payload.get("series") or []
    series: list[dict[str, Any]] = []
    for s in raw_series:
        points = [
            [p[0], p[1] / factor]
            for p in (s.get("points") or [])
            if isinstance(p, (list, tuple)) and len(p) >= 2 and p[1] is not None
        ]
        series.append({"name": str(s.get("name") or ""), "points": points})
    if not any(s["points"] for s in series):
        logger.warning("曲线无数据点，生成占位图 metric=%s period=%s", fact.name, fact.period)
        return _render_placeholder("暂无数据", CURVE_SIZE_MM, dpi)

    precision = fact.display_precision
    fig, ax = plt.subplots(figsize=_mm_to_px(CURVE_SIZE_MM, dpi))
    for i, s in enumerate(series):
        color = SERIES_COLORS[i % len(SERIES_COLORS)]
        # matplotlib 日期轴的单位是「距 1970-01-01 的天数」，unix 秒必须先换算，
        # 否则数值约 1.8e9 天会直接 OverflowError
        xs = [p[0] / SECONDS_PER_DAY for p in s["points"]]
        ys = [p[1] for p in s["points"]]
        ax.plot(xs, ys, color=color, linewidth=1.4, label=s["name"])
        # 单点数据画不出线，补一个标记点
        if len(xs) == 1:
            ax.plot(xs, ys, marker="o", markersize=3.5, color=color)

    ax.set_title(fact.name, fontsize=9, color="#0F172A", pad=6)
    # Y 轴单位同样用展示口径（如 bps -> Kbps），未换算时回退原始单位
    display_unit = fact.display_unit if fact.display_unit is not None else fact.unit
    if display_unit:
        ax.set_ylabel(display_unit, fontsize=7, color="#475569")
    # X 轴日期刻度（rcParams.timezone 默认 UTC，与上面的天数换算一致）
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos: compact_number(v)))
    ax.yaxis.set_major_locator(MaxNLocator(6))
    ax.grid(True, linestyle="--", linewidth=0.4, color="#E2E8F0")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#CBD5E1")
    ax.tick_params(labelsize=6, colors="#64748B")
    _annotate(ax, series, precision)
    legend = ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.16),
        ncol=min(len(series), 4),
        fontsize=6,
        frameon=False,
    )
    for text in legend.get_texts():
        text.set_color("#475569")
    fig.tight_layout(pad=0.6)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _render_pie(
    payload: dict[str, Any], fact: MetricFact, dpi: int, labels: tuple[str, str] | None
) -> bytes:
    """饼图：单 item 的 UP/DOWN 小时数占比。

    labels 优先用调用方从 pie_config 推断的语义（icmpping 这类 1/0 型 -> 通/断），
    推断不出时用 PIE_DEFAULT_LABELS。百分比标签按 display_precision（None -> 1 位）。
    """
    up_count = int(payload.get("up_count") or 0)
    down_count = int(payload.get("down_count") or 0)
    total = up_count + down_count
    if total == 0:
        logger.warning("饼图无有效小时数，生成占位图 metric=%s period=%s", fact.name, fact.period)
        return _render_placeholder("暂无数据", PIE_SIZE_MM, dpi)

    # 标签优先级：pie payload（后端 calc 推断，随 fact 下发）> 调用方参数 > 默认
    up_label = str(payload.get("up_label") or (labels[0] if labels else "") or PIE_DEFAULT_LABELS[0])
    down_label = str(
        payload.get("down_label") or (labels[1] if labels else "") or PIE_DEFAULT_LABELS[1]
    )
    up_pct = float(payload.get("up_pct") or 0.0)
    down_pct = float(payload.get("down_pct") or 0.0)
    # 后端 up_pct/down_pct 已按指标精度算过；这里只做展示位数收敛
    digits = 1 if fact.display_precision is None else max(0, min(6, fact.display_precision))

    fig, ax = plt.subplots(figsize=_mm_to_px(PIE_SIZE_MM, dpi))
    wedges, _ = ax.pie(
        [up_count, down_count],
        colors=list(PIE_COLORS),
        startangle=90,
        counterclock=False,
        wedgeprops={"linewidth": 1.0, "edgecolor": "white"},
    )
    # 百分比标签放在扇区内侧。**计数为 0 的扇区不画内部标签**：
    # matplotlib 对零宽扇区仍会生成 wedge，其 theta1==theta2，标签会被放到
    # 12 点位置压在相邻扇区上（100% 通时出现 "0.0% 0 小时" 浮在绿块上）。
    # 0 值信息由图例的 "断 0 小时" 表达，不重复标注。
    for wedge, pct, color, count in (
        (wedges[0], up_pct, PIE_COLORS[0], up_count),
        (wedges[1], down_pct, PIE_COLORS[1], down_count),
    ):
        if count <= 0:
            continue
        ang = (wedge.theta2 + wedge.theta1) / 2.0
        x = np.cos(np.deg2rad(ang))
        y = np.sin(np.deg2rad(ang))
        ax.text(
            x * 0.62,
            y * 0.62,
            f"{pct:.{digits}f}%\n{count} 小时",
            ha="center",
            va="center",
            fontsize=6,
            color="white",
            linespacing=1.4,
        )
    ax.set_title(fact.name, fontsize=9, color="#0F172A", pad=4)
    legend = ax.legend(
        wedges,
        [f"{up_label} {up_count} 小时", f"{down_label} {down_count} 小时"],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.02),
        ncol=2,
        fontsize=6,
        frameon=False,
    )
    for text in legend.get_texts():
        text.set_color("#475569")
    fig.tight_layout(pad=0.5)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def render_fact_chart(
    fact: MetricFact,
    *,
    dpi: int = DPI,
    pie_labels: tuple[str, str] | None = None,
) -> bytes:
    """按 fact.value_shape 渲染 PNG（series -> 折线，pie -> 饼图）。

    pie_labels：由调用方从 metric.type_config.pie_config 推断的 (UP 标签, DOWN
    标签)；为 None 时用 PIE_DEFAULT_LABELS（通/断）。chart_renderer 不读
    type_config —— 那是指标语义，不属于渲染层职责。
    """
    _ensure_font_configured()
    started = time.perf_counter()
    shape = fact.value_shape
    if shape == "series":
        data = _render_curve(fact.value if isinstance(fact.value, dict) else {}, fact, dpi)
    elif shape == "pie":
        data = _render_pie(
            fact.value if isinstance(fact.value, dict) else {},
            fact,
            dpi,
            pie_labels,
        )
    else:
        raise ChartRenderError(
            f"value_shape={shape} 不是图表类型（series/pie），不应调用图表渲染"
        )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "chart rendered: shape=%s metric=%s bytes=%d elapsed=%dms",
        shape,
        fact.name,
        len(data),
        elapsed_ms,
    )
    return data
