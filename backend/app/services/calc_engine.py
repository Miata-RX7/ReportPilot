"""指标计算引擎：facts 的唯一来源（AI 不参与任何指标计算）。

二次聚合口径（两阶段，顺序不可颠倒；本期与基线必须同列同 agg）：
- 阶段一（时间聚合，per item）：单个 item 在区间内的每小时趋势值按 agg 聚合
- 阶段二（空间聚合，跨 item）：所有 item 的周期值再用同一 agg 聚合
列选择规则：
- agg=avg -> 对每小时 value_avg 再取平均；agg=sum -> 对每小时 value_avg 求和
- agg=max -> 对每小时 value_max 再取最大；agg=min -> 对每小时 value_min 再取最小
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from dateutil.relativedelta import relativedelta
from pydantic import ValidationError

from app.models.metric import MetricDefinition
from app.schemas.metric import (
    TimeConfig,
    CurveSeries,
    CurveTypeConfig,
    MetricFact,
    PieConfig,
    TextConfig,
    Transform,
)
from app.services.adapters.base import AdapterError, DataSourceAdapter
from app.services.period_utils import (
    baseline_range,
    period_to_unix_range,
    shift_period,
)
from app.services.unit_scale import UnitScale, display_unit_of

logger = logging.getLogger(__name__)


class MetricComputeError(Exception):
    """指标计算失败（配置非法 / 数据缺失）。消息不含敏感信息。"""


AGG_TO_COLUMN = {
    "avg": "value_avg",
    "sum": "value_avg",
    "max": "value_max",
    "min": "value_min",
}
COMPARE_TYPES = ("none", "mom", "yoy")


def _aggregate(values: list[float], agg: str) -> float:
    if agg == "sum":
        return float(sum(values))
    if agg == "avg":
        return float(sum(values) / len(values))
    if agg == "max":
        return float(max(values))
    if agg == "min":
        return float(min(values))
    raise MetricComputeError(f"不支持的聚合方式: {agg}")


def _two_stage_aggregate(
    trend: list[dict[str, Any]], agg: str, stage_label: str
) -> tuple[float, int]:
    """两阶段聚合，返回 (指标值, 参与聚合的 item 数)。"""
    if not trend:
        raise MetricComputeError(f"{stage_label}时间区间内无趋势数据")
    column = AGG_TO_COLUMN[agg]
    by_item: dict[int, list[float]] = {}
    for row in trend:
        by_item.setdefault(row["item_id"], []).append(row[column])
    # 阶段一：per item 时间聚合
    per_item = [_aggregate(vals, agg) for vals in by_item.values()]
    # 阶段二：跨 item 空间聚合
    return _aggregate(per_item, agg), len(per_item)


def _parse_ids(raw: Any, field: str) -> list[int]:
    """把 query_config 里的 id 列表转成 int 列表，容忍脏值（空串忽略）。"""
    values = [str(v).strip() for v in (raw or [])]
    values = [v for v in values if v]
    try:
        return [int(v) for v in values]
    except ValueError:
        raise MetricComputeError(f"query_config.{field} 含非数字项: {values}") from None


def _clean_keys(raw: Any, field: str) -> list[str]:
    """取数对象的**定位串**列表（去空白、丢空项）。

    Zabbix 的定位串就是数字 item_id 字符串；AIOps 的是四元组编码串
    （见 services/aiops_keys.py）。这里只做清洗，不解释内容 ——
    解释是 adapter 的事。
    """
    out: list[str] = []
    for value in raw or []:
        token = str(value).strip()
        if token and token not in out:
            out.append(token)
    return out


def _scope_ids(
    query_config: dict[str, Any], adapter: DataSourceAdapter
) -> tuple[list[str], list[str]]:
    """按 adapter 的范围语义读出 (上级, 下级) 两组 id。

    Zabbix读主机组/主机，AIOps 读模型/实例。**刻意不在这里判断哪组必填** ——
    Pydantic 层已保证「至少一组非空且不半截」，精确归属由 adapter 校验。
    """
    qc = query_config or {}
    if getattr(adapter, "scope_kind", "host") == "model_instance":
        upper = _clean_keys(qc.get("model_ids"), "model_ids")
        lower = _clean_keys(qc.get("instance_ids"), "instance_ids")
    else:
        upper = _clean_keys(qc.get("host_group_ids"), "host_group_ids")
        lower = _clean_keys(qc.get("host_ids"), "host_ids")
    return upper, lower


def _scope_labels(adapter: DataSourceAdapter) -> tuple[str, str]:
    """范围称谓前缀 (上级, 下级)，用在 formula / object 文案里。

    Zabbix -> ("组", "主机")；AIOps -> ("模型", "实例")。
    """
    if getattr(adapter, "scope_kind", "host") == "model_instance":
        return "模型", "实例"
    return "组", "主机"


def _scope_hints(adapter: DataSourceAdapter) -> tuple[str, str]:
    """名称获取失败时的降级提示 (上级, 下级)。

    Zabbix 沿用既有的 ``group`` / ``host``（已被
    ``test_groups_failure_degrades_object`` 锁定，不做无谓改动）；AIOps 用中文。
    """
    if getattr(adapter, "scope_kind", "host") == "model_instance":
        return "模型", "实例"
    return "group", "host"


async def resolve_items(
    metric_def: MetricDefinition, adapter: DataSourceAdapter
) -> list[dict[str, Any]]:
    """按 query_config 解析目标监控项（scalar / pie / text 共用 item_ids 形态）。

    语义：取数对象 = host_ids 指定的主机集合；监控项集合 = item_ids 直接指定。
    两者必须自洽——host_ids 需属于 host_group_ids（防御性校验，防绕过前端），
    且 item_ids 必须能在这些主机上找到。

    text + mode=fixed 允许 item_ids 为空（不取数），此时返回 []。
    curve 类型走 series 配置，用 resolve_series_items。
    """
    qc = metric_def.query_config or {}
    raw_items = qc.get("item_ids") or []
    if not raw_items:
        # 仍需校验取数范围（text fixed 不取数但配置必须自洽）
        await _validate_host_scope(qc, adapter)
        return []
    return await _resolve_scoped_items(qc, adapter, _clean_keys(raw_items, "item_ids"))


async def resolve_series_items(
    metric_def: MetricDefinition, adapter: DataSourceAdapter
) -> tuple[list[dict[str, Any]], list["CurveSeries"]]:
    """curve：解析 series 形态的监控项。

    返回 (命中的 item 元信息列表, 校验通过的 CurveSeries 列表)，
    顺序与 query_config.series 一致（保证曲线顺序稳定）。
    """
    qc = metric_def.query_config or {}
    raw_series = qc.get("series") or []
    if not raw_series:
        raise MetricComputeError("curve 至少需要一条曲线（query_config.series）")
    parsed: list[CurveSeries] = []
    for idx, raw in enumerate(raw_series):
        try:
            parsed.append(CurveSeries.model_validate(raw))
        except ValidationError as exc:
            raise MetricComputeError(f"query_config.series[{idx}] 非法: {exc}") from None
    ids = [s.item_id for s in parsed]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise MetricComputeError(f"同一监控项不能重复配置多条曲线: {dup}")
    items = await _resolve_scoped_items(qc, adapter, [s.item_id for s in parsed])
    return items, parsed


async def _validate_host_scope(
    query_config: dict[str, Any], adapter: DataSourceAdapter
) -> tuple[list[int], list[int]]:
    """校验取数范围必填且下级属于所选上级（防绕过前端直接构造 query_config）。

    范围语义由 ``adapter.scope_kind`` 决定：Zabbix 是主机组/主机，AIOps 是模型/实例。
    """
    upper, lower = _scope_ids(query_config, adapter)
    label = (
        "模型/实例"
        if getattr(adapter, "scope_kind", "host") == "model_instance"
        else "主机组/主机"
    )
    for field, values in (("上级", upper), ("下级", lower)):
        if not values:
            raise MetricComputeError(f"query_config 取数范围{field}不能为空（{label}）")

    hosts = await adapter.list_hosts(upper)
    valid_host_ids = {str(h["host_id"]) for h in hosts}
    unknown = sorted(set(lower) - valid_host_ids)
    if unknown:
        raise MetricComputeError(f"以下下级不属于所选{label} {upper}: {unknown}")
    try:
        return [int(x) for x in upper], [int(x) for x in lower]
    except ValueError:
        raise MetricComputeError(f"取数范围含非数字 id: {upper + lower}") from None


async def _resolve_scoped_items(
    query_config: dict[str, Any], adapter: DataSourceAdapter, keys: list[str]
) -> list[dict[str, Any]]:
    """在指定范围内解析取数对象，产出带**整数 id** 的元信息列表。

    范围校验与定位串解析都交给 adapter（``resolve_scoped_items``）——
    Zabbix 的「定位串 == 数字 item_id」与 AIOps 的四元组编码差异很大，
    放在这里分支会让 calc_engine 变成数据源方言的堆积场。

    adapter 抛的 ``AdapterError`` 转成 ``MetricComputeError``，保持 422 语义
    （AdapterError 默认会被映射成 502「数据源侧问题」）。
    """
    upper, lower = _scope_ids(query_config, adapter)
    if not upper or not lower:
        await _validate_host_scope(query_config, adapter)
    try:
        return await adapter.resolve_scoped_items(
            upper_ids=upper, lower_ids=lower, keys=keys
        )
    except AdapterError as exc:
        raise MetricComputeError(str(exc)) from None


async def _resolve_scope_names(
    adapter: DataSourceAdapter,
    query_config: dict[str, Any],
    items: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """解析上级与下级的名称（供 object / formula 证据描述）。

Zabbix 是「主机组 / 主机」，AIOps 是「模型 / 实例」—— 文案跟着
``adapter.scope_kind`` 走，否则月报里会出现「模型: 路由交换 / 主机: xxx」
这种把实例叫成主机的错话。
数据源不可达时降级为 "#id (名称获取失败)"；Zabbix 侧沿用既有的
``group #id`` / ``host #id`` 文案（已被单测锁定，不做无谓改动）。
"""
    upper_hint, lower_hint = _scope_hints(adapter)

    upper_ids, lower_ids = _scope_ids(query_config, adapter)
    try:
        groups = await adapter.list_host_groups()
        name_map = {str(g["group_id"]): g["name"] for g in groups}
        upper_names = [
            name_map.get(gid, f"{upper_hint} #{gid} (名称获取失败)")
            for gid in upper_ids
        ]
    except AdapterError:
        logger.warning("list_host_groups 失败，范围描述降级为 ID")
        upper_names = [f"{upper_hint} #{gid} (名称获取失败)" for gid in upper_ids]

    lower_name_map: dict[str, str] = {}
    for i in items:
        hid = i.get("host_id")
        if hid is None or str(hid) in lower_name_map:
            continue
        lower_name_map[str(hid)] = i.get("host") or f"{lower_hint} #{hid}"
    lower_names = [
        lower_name_map.get(hid, f"{lower_hint} #{hid}") for hid in lower_ids
    ]
    return upper_names, lower_names


def _build_object_desc(
    group_names: list[str],
    items: list[dict[str, Any]],
    host_noun: str = "台主机",
) -> str:
    """组装 object 证据描述："组名（N 台主机, M 个监控项）"。

    ``host_noun`` 带量词，供 AIOps 传「个实例」（"台实例" 不通顺）。
    """
    n_hosts = len({i.get("host_id") for i in items})
    return f"{', '.join(group_names)}（{n_hosts} {host_noun}, {len(items)} 个监控项）"


def _build_formula_desc(
    agg: str,
    compare: str,
    lookback: int | None,
    n_items: int,
    group_names: list[str],
    host_names: list[str],
    upper_label: str = "组",
    lower_label: str = "主机",
) -> str:
    """组装 formula 证据描述（可追溯实际口径）。"""
    column = AGG_TO_COLUMN[agg]
    scope = (
        f"{upper_label}: {', '.join(group_names)}; "
        f"{lower_label}: {', '.join(host_names)}"
    )
    desc = f"{agg}(trend.hourly.{column}) → {agg} over {n_items} items [{scope}]"
    if compare != "none":
        desc += f" → {compare}({lookback} calendar month)"
    return desc


async def compute_metric(
    metric_def: MetricDefinition,
    period: str,
    adapter: DataSourceAdapter | None,
    source_name: str,
) -> MetricFact:
    """按 metric_type 分发计算分支，返回 MetricFact（不落库）。

    adapter / source_name 对 time 类型为 None（不取数，与数据源无关）。

    出口统一附加「展示派生字段」（单位换算，实现见 services/unit_scale.py）：本函数是
    试算 API 与报告/预览的**唯一**公共出口，改这一处即覆盖全链路，避免「改了四处漏第五处」。
    """
    metric_type = metric_def.metric_type or "scalar"
    if metric_type == "time":
        fact = await _compute_time(metric_def, period)
    elif metric_type == "scalar":
        fact = await _compute_scalar(metric_def, period, adapter, source_name)
    elif metric_type == "text":
        fact = await _compute_text(metric_def, period, adapter, source_name)
    elif metric_type == "curve":
        fact = await _compute_curve(metric_def, period, adapter, source_name)
    elif metric_type == "pie":
        fact = await _compute_pie(metric_def, period, adapter, source_name)
    else:
        raise MetricComputeError(f"未知的 metric_type: {metric_type}")
    _apply_unit_scale(fact, metric_def)
    return fact


def _series_magnitude(payload: Any) -> float:
    """曲线全部数据点的最大绝对值（统一选档用）；无有效点返回 0.0（-> 0 档 = 原单位）。"""
    if not isinstance(payload, dict):
        return 0.0
    best = 0.0
    for series in payload.get("series") or []:
        if not isinstance(series, dict):
            continue
        for point in series.get("points") or []:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            v = point[1]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            best = max(best, abs(float(v)))
    return best


def _apply_unit_scale(fact: MetricFact, metric_def: MetricDefinition) -> None:
    """在事实层之外附加「展示派生字段」（换算实现见 services/unit_scale.py）。

    事实层（value / baseline_value / change_pct）一律不动，因此环比与显著波动判定
    结果与升级前逐位一致；这里只回答「怎么展示」。

    仅 scalar / curve 生效（写路径已拒绝其余类型，这里再兜一层防御）：
    - scalar：以**本期值**选档，基线复用同一因子（否则本期 M 档、基线 K 档，环比失真）
    - curve：以全部点的最大绝对值统一选档（一张图内不能混用多个档位）
    """
    scale = UnitScale.parse(metric_def.unit_config)
    if not scale.enabled:
        return
    metric_type = metric_def.metric_type or "scalar"
    if metric_type == "curve":
        magnitude = _series_magnitude(fact.value)
    elif metric_type == "scalar":
        if isinstance(fact.value, bool) or not isinstance(fact.value, (int, float)):
            return  # 防御：scalar 分支必然返回 float
        magnitude = float(fact.value)
    else:
        return

    resolved = scale.resolve(
        magnitude=magnitude,
        base_unit=metric_def.unit,
        precision=metric_def.display_precision,
    )
    if resolved is None:  # pragma: no cover - enabled 为真时必定返回
        return
    fact.display_value = resolved.apply(magnitude) if metric_type == "scalar" else None
    fact.display_baseline_value = resolved.apply(fact.baseline_value)
    fact.display_unit = display_unit_of(resolved, metric_def.unit)
    fact.display_factor = resolved.factor
    # 换算开启且指标未配精度时补默认展示位数（2 位），让渲染层无需再分支
    fact.display_precision = resolved.precision
    if metric_type == "scalar" and fact.formula:
        fact.formula = f"{fact.formula}；展示口径 {resolved.describe()}"
    logger.info(
        "指标展示口径已换算 name=%s type=%s factor=%g unit=%s",
        metric_def.name,
        metric_type,
        resolved.factor,
        fact.display_unit,
    )


# --------------------------------------------------------------------------
# 时间类型
# --------------------------------------------------------------------------
# 字段模块 -> datetime 属性名
TIME_FIELD_ATTRS = {"年": "year", "月": "month", "日": "day", "时": "hour", "分": "minute", "秒": "second"}
# 字面量模块 -> 输出文本
TIME_LITERAL_CHARS = {"空格": " ", "-": "-", "/": "/", ":": ":", ".": "."}


def format_time_modules(
    modules: list[str], dt: datetime, zero_pad: bool = True
) -> str:
    """按模块列表把 datetime 渲染成文本。

    - 年恒为 4 位；月/日/时/分/秒在 zero_pad=True 时补零（2026-09），否则不补（2026-9）
    - 未知模块按字面量原样输出（不静默吞掉用户配置）
    前端 utils/time.ts 有同名实现，语义必须一致（两侧都有单测锁定）。
    """
    out: list[str] = []
    for m in modules:
        attr = TIME_FIELD_ATTRS.get(m)
        if attr:
            v = getattr(dt, attr)
            if m == "年":
                out.append(f"{v:04d}")
            elif zero_pad:
                out.append(f"{v:02d}")
            else:
                out.append(str(v))
            continue
        out.append(TIME_LITERAL_CHARS.get(m, m))
    return "".join(out)


def _time_config_of(metric_def: MetricDefinition) -> TimeConfig:
    cfg = (metric_def.type_config or {}).get("time_config") or {}
    try:
        return TimeConfig.model_validate(cfg)
    except ValidationError as exc:
        raise MetricComputeError(f"time_config 非法: {exc}") from None


def shift_time(base: datetime, value: int, unit: str) -> datetime:
    """按天/月平移。月偏移用 relativedelta，自动夹取月末（3-31 -1月 -> 2-28）。"""
    if value == 0:
        return base
    if unit == "month":
        return base + relativedelta(months=value)
    return base + timedelta(days=value)


def quick_target_time(base: datetime, kind: str) -> datetime:
    """快速偏移：按**计算时刻**解析为具体日期。

    必须在这里（而非前端）解析：偏移是相对"生成报告那一刻"的，
    若在前端换算成固定天数，报告重新生成时会算错。

    - 上周周一/周末：以 base 所在周的周一为基准往前 7 天（周日 = 周一+6）
    - 上月一号：本月 1 号往前一个月；上月月末：本月 1 号减 1 天
    """
    if kind == "last_week_monday":
        return base - timedelta(days=base.weekday() + 7)
    if kind == "last_week_sunday":
        return base - timedelta(days=base.weekday() + 1)
    if kind == "last_month_first":
        return base.replace(day=1) - relativedelta(months=1)
    if kind == "last_month_end":
        return base.replace(day=1) - timedelta(days=1)
    raise MetricComputeError(f"未知的 quick_offset: {kind}")


def resolve_time_target(base: datetime, cfg: TimeConfig) -> datetime:
    """按配置解析目标时间（手动偏移 or 快速偏移）。"""
    if cfg.offset_mode == "quick":
        return quick_target_time(base, cfg.quick_offset or "")
    return shift_time(base, cfg.offset_value, cfg.offset_unit)


async def _compute_time(
    metric_def: MetricDefinition,
    period: str,
    now: datetime | None = None,
) -> MetricFact:
    """time：当前时间 -> 按 offset 平移 -> 按 format_modules 格式化。

    基准是**计算时刻**（不是统计周期），因此同一指标在不同时刻试算可能得到
    不同结果；这是"实时时间"语义的固有特性，formula 里会写明偏移量。
    """
    cfg = _time_config_of(metric_def)
    base = now or datetime.now()
    target = resolve_time_target(base, cfg)
    text = format_time_modules(cfg.format_modules, target, cfg.zero_pad)
    pattern = "".join(TIME_LITERAL_CHARS.get(m, m) for m in cfg.format_modules)
    logger.info(
        "时间计算完成 name=%s base=%s offset=%s -> %s",
        metric_def.name,
        base.isoformat(timespec="seconds"),
        cfg.label(),
        text,
    )
    return MetricFact(
        metric_id=metric_def.id,
        name=metric_def.name,
        value={"text": text},
        value_shape="text",
        baseline_value=None,
        change_pct=None,
        unit=metric_def.unit,
        source="当前时间",
        object=(
            f"基准=计算时刻({base.strftime('%Y-%m-%d %H:%M:%S')})"
            f"，偏移={cfg.label()}"
        ),
        formula=f"now({cfg.label()}) -> format({pattern!r})",
        period=period,
    )


def _percent(value: float, digits: int) -> float:
    """按 display_precision 规整百分比位数（None -> 1 位，与 scalar 精度机制统一）。"""
    return round(value, digits)


def _precision_of(metric_def: MetricDefinition) -> int:
    """百分比默认 1 位；display_precision=N 时用 N 位。"""
    return 1 if metric_def.display_precision is None else int(metric_def.display_precision)


def _format_pie_value(v: float) -> str:
    """把数值转成可与 up_values/down_values 比较的字符串（整数去掉小数尾巴）。"""
    if float(v).is_integer():
        return str(int(v))
    return repr(float(v))


def _transform_of(metric_def: MetricDefinition) -> Transform:
    """从 type_config 解析数值变换（缺省恒等）。"""
    cfg = (metric_def.type_config or {}).get("transform") or {}
    try:
        return Transform.model_validate(cfg)
    except ValidationError as exc:
        raise MetricComputeError(f"type_config.transform 非法: {exc}") from None


async def _compute_curve(
    metric_def: MetricDefinition,
    period: str,
    adapter: DataSourceAdapter,
    source_name: str,
) -> MetricFact:
    """curve：每条 series 独立成一条时序曲线。

    - 所有 series 的 item_id **合并成一次 trend.get**（N 条曲线仍 1 次 RPC）
    - 逐点应用该 series 自己的 transform
    - points 按 unix_ts **升序**；缺失小时**不补点**（不插 null），由前端 time 轴处理
    - 不做基线对比（曲线无同比/环比语义），baseline/change 恒为 None
    """
    try:
        start, end = period_to_unix_range(period)
    except ValueError as exc:
        raise MetricComputeError(str(exc)) from exc

    try:
        type_cfg = CurveTypeConfig.model_validate(metric_def.type_config or {})
    except ValidationError as exc:
        raise MetricComputeError(f"type_config 非法: {exc}") from None

    raw_series = (metric_def.query_config or {}).get("series") or []
    if len(raw_series) > type_cfg.max_series:
        raise MetricComputeError(
            f"曲线条数 {len(raw_series)} 超过上限 {type_cfg.max_series}"
        )
    items, series_list = await resolve_series_items(metric_def, adapter)

    item_ids = [i["item_id"] for i in items]
    trend = await adapter.fetch_trend_data(item_ids, start, end)
    if not trend:
        raise MetricComputeError("本期时间区间内无趋势数据")

    # 一次拉取后按 item_id 分组（每组内按 clock 升序）
    by_item: dict[int, list[tuple[int, float]]] = {}
    for row in trend:
        by_item.setdefault(row["item_id"], []).append((row["clock"], row["value_avg"]))

    meta_by_id = {i["item_id"]: i for i in items}
    default_tf = type_cfg.transform_default
    # items 与 series_list 顺序一一对应（resolve_series_items 保证顺序稳定），
    # item_id 已由 adapter 归一为整数 —— 这里不能再 int(s.item_id)，
    # 因为 AIOps 的定位串不是数字。
    synthetic_ids = [int(i["item_id"]) for i in items]
    payload_series: list[dict[str, Any]] = []
    for idx, s in enumerate(series_list):
        iid = synthetic_ids[idx] if idx < len(synthetic_ids) else idx
        tf = s.transform or default_tf
        raw_points = sorted(by_item.get(iid, []), key=lambda p: p[0])
        meta = meta_by_id.get(iid, {})
        payload_series.append(
            {
                "name": s.display_name or meta.get("name") or f"item-{s.item_id}",
                "item_id": str(s.item_id),
                "points": [[ts, tf.apply(v)] for ts, v in raw_points],
            }
        )

    qc = metric_def.query_config or {}
    group_names, host_names = await _resolve_scope_names(adapter, qc, items)
    upper_label, lower_label = _scope_labels(adapter)
    host_noun = "台主机" if lower_label == "主机" else "个实例"
    n_hosts = len({i.get("host_id") for i in items})
    object_desc = (
        f"{', '.join(group_names)}（{n_hosts} {host_noun}, {len(payload_series)} 条曲线）"
    )
    parts: list[str] = []
    for idx, sc in enumerate(series_list):
        tf = sc.transform or default_tf
        sid = synthetic_ids[idx] if idx < len(synthetic_ids) else idx
        seg = f"{sc.display_name or f'item-{sc.item_id}'}: {len(by_item.get(sid, []))} 点"
        if not tf.is_identity:
            seg += f" ×{tf.mul} + {tf.add}"
        parts.append(seg)
    formula_desc = (
        "raw(trend.hourly.value_avg) per series ["
        f"{upper_label}: {', '.join(group_names)}; "
        f"{lower_label}: {', '.join(host_names)}] → " + "; ".join(parts)
    )

    total_points = sum(len(s["points"]) for s in payload_series)
    logger.info(
        "指标计算完成 name=%s type=curve period=%s series=%d points=%d",
        metric_def.name,
        period,
        len(payload_series),
        total_points,
    )
    return MetricFact(
        metric_id=metric_def.id,
        name=metric_def.name,
        value={"series": payload_series},
        value_shape="series",
        baseline_value=None,
        change_pct=None,
        unit=metric_def.unit,
        source=source_name,
        object=object_desc,
        formula=formula_desc,
        period=period,
    )


def pie_labels(metric_def: MetricDefinition) -> tuple[str, str]:
    """推断饼图的 (UP 标签, DOWN 标签)。

    1/0 型监控项（icmpping 等）语义明确 -> 「通」「断」，比 UP/DOWN 直观；
    其它取值组合无法可靠推断语义 -> 回退英文 UP/DOWN，避免写出错误标签。
    图表渲染层不读 type_config（那是指标语义，不属于渲染职责），由调用方
    推断后以 pie_labels 参数传入。
    """
    cfg = (metric_def.type_config or {}).get("pie_config") or {}
    up = {str(v) for v in (cfg.get("up_values") or [])}
    down = {str(v) for v in (cfg.get("down_values") or [])}
    if up == {"1"} and down == {"0"}:
        return ("通", "断")
    return ("UP", "DOWN")


async def _compute_pie(
    metric_def: MetricDefinition,
    period: str,
    adapter: DataSourceAdapter,
    source_name: str,
) -> MetricFact:
    """pie：单 item 的 UP/DOWN 小时数占比。

    统计口径用 **value_max**（而非 value_avg）：0/1 型 item 在某小时内既有 0 又有 1 时，
    value_avg 会得到 0.5，使"小时数占比"失真；value_max 视该小时为出现过非零（UP）。

    unknown_mode：
    - exclude：未命中 up/down 的小时不计入分母
    - error  ：出现未命中值直接报错（数据源口径与配置不符）
    """
    try:
        start, end = period_to_unix_range(period)
    except ValueError as exc:
        raise MetricComputeError(str(exc)) from exc

    try:
        pie_cfg = PieConfig.model_validate(
            (metric_def.type_config or {}).get("pie_config") or {}
        )
    except ValidationError as exc:
        raise MetricComputeError(f"type_config.pie_config 非法: {exc}") from None

    items = await resolve_items(metric_def, adapter)
    if len(items) != 1:
        raise MetricComputeError("pie 指标必须且只能配置一个监控项")
    item_id = items[0]["item_id"]

    trend = await adapter.fetch_trend_data([item_id], start, end)
    if not trend:
        raise MetricComputeError("本期时间区间内无趋势数据")

    up_set = set(pie_cfg.up_values)
    down_set = set(pie_cfg.down_values)
    up_count = 0
    down_count = 0
    unknown_count = 0
    first_unknown: float | None = None
    for row in trend:
        raw = _format_pie_value(row["value_max"])
        if raw in up_set:
            up_count += 1
        elif raw in down_set:
            down_count += 1
        else:
            unknown_count += 1
            if first_unknown is None:
                first_unknown = row["value_max"]
            if pie_cfg.unknown_mode == "error":
                raise MetricComputeError(
                    f"监控项 {item_id} 出现未归类的值 {raw}（unknown_mode=error）"
                )

    total = up_count + down_count
    digits = _precision_of(metric_def)
    if total == 0:
        up_pct = down_pct = 0.0
    else:
        up_pct = _percent(up_count / total * 100, digits)
        down_pct = _percent(down_count / total * 100, digits)

    qc = metric_def.query_config or {}
    group_names, host_names = await _resolve_scope_names(adapter, qc, items)
    upper_label, lower_label = _scope_labels(adapter)
    host_noun = "台主机" if lower_label == "主机" else "个实例"
    object_desc = _build_object_desc(group_names, items, host_noun=host_noun)
    formula_desc = (
        f"count(hour value_max∈{{{','.join(pie_cfg.up_values)}}})={up_count} / "
        f"count(hour value_max∈{{{','.join(pie_cfg.down_values)}}})={down_count}"
        f" → 占比 [{upper_label}: {', '.join(group_names)}; "
        f"{lower_label}: {', '.join(host_names)}]"
    )
    if unknown_count:
        formula_desc += f"（未归类 {unknown_count} 小时已排除）"

    logger.info(
        "指标计算完成 name=%s type=pie period=%s up=%s down=%s unknown=%s",
        metric_def.name,
        period,
        up_count,
        down_count,
        unknown_count,
    )
    up_label, down_label = pie_labels(metric_def)
    return MetricFact(
        metric_id=metric_def.id,
        name=metric_def.name,
        value={
            "up_count": up_count,
            "down_count": down_count,
            "up_pct": up_pct,
            "down_pct": down_pct,
            # 显示标签随 payload 下发：报告 PNG 与前端试算共用同一份口径，
            # 避免出现"报告里写通/断、页面上写 UP/DOWN"的不一致
            "up_label": up_label,
            "down_label": down_label,
        },
        value_shape="pie",
        baseline_value=None,
        change_pct=None,
        unit=metric_def.unit,
        source=source_name,
        object=object_desc,
        formula=formula_desc,
        period=period,
    )


async def _compute_scalar(
    metric_def: MetricDefinition,
    period: str,
    adapter: DataSourceAdapter,
    source_name: str,
) -> MetricFact:
    """scalar：两阶段聚合 -> apply(transform) -> 与基线同口径变换。

    边界条件：
    - 本期 trend 为空 -> MetricComputeError（不静默返回 0）
    - 基线 trend 为空 -> 仅告警，baseline_value/change_pct 置 None
    - baseline_value == 0 -> change_pct 置 None（不返回 inf/nan）
    - change_pct 基于**变换后**的数值计算
    """
    formula_cfg = metric_def.calc_formula or {}
    agg = formula_cfg.get("agg", "avg")
    compare = formula_cfg.get("compare", "none")
    lookback = formula_cfg.get("baseline_lookback")
    if agg not in AGG_TO_COLUMN:
        raise MetricComputeError(f"calc_formula.agg 非法: {agg}")
    if compare not in COMPARE_TYPES:
        raise MetricComputeError(f"calc_formula.compare 非法: {compare}")
    try:
        start, end = period_to_unix_range(period)
    except ValueError as exc:
        raise MetricComputeError(str(exc)) from exc
    if compare != "none" and (not isinstance(lookback, int) or lookback < 1):
        raise MetricComputeError(
            "compare 非 none 时 baseline_lookback 必须为 >=1 的整数"
        )

    items = await resolve_items(metric_def, adapter)
    if not items:
        raise MetricComputeError("scalar 指标至少需要一个监控项")
    item_ids = [i["item_id"] for i in items]
    transform = _transform_of(metric_def)

    trend = await adapter.fetch_trend_data(item_ids, start, end)
    raw_value, n_items = _two_stage_aggregate(trend, agg, "本期")
    value = transform.apply(raw_value)

    baseline_value: float | None = None
    change_pct: float | None = None
    if compare != "none":
        b_start, b_end = baseline_range(period, lookback)
        b_trend = await adapter.fetch_trend_data(item_ids, b_start, b_end)
        if b_trend:
            raw_base, _ = _two_stage_aggregate(b_trend, agg, "基线")
            baseline_value = transform.apply(raw_base)
            if baseline_value != 0:
                change_pct = (value - baseline_value) / baseline_value * 100
        else:
            logger.warning(
                "指标 %s 基线区间（%s）无趋势数据，跳过对比",
                metric_def.name,
                shift_period(period, -lookback),
            )

    qc = metric_def.query_config or {}
    group_names, host_names = await _resolve_scope_names(adapter, qc, items)
    upper_label, lower_label = _scope_labels(adapter)
    host_noun = "台主机" if lower_label == "主机" else "个实例"
    object_desc = _build_object_desc(group_names, items, host_noun=host_noun)
    formula_desc = _build_formula_desc(
        agg,
        compare,
        lookback,
        n_items,
        group_names,
        host_names,
        upper_label=upper_label,
        lower_label=lower_label,
    )
    if not transform.is_identity:
        formula_desc += f" → ×{transform.mul} + {transform.add}"

    logger.info(
        "指标计算完成 name=%s type=scalar period=%s value=%s baseline=%s",
        metric_def.name,
        period,
        value,
        baseline_value,
    )
    return MetricFact(
        metric_id=metric_def.id,
        name=metric_def.name,
        value=value,
        value_shape="scalar",
        baseline_value=baseline_value,
        change_pct=change_pct,
        unit=metric_def.unit,
        source=source_name,
        object=object_desc,
        formula=formula_desc,
        period=period,
    )


async def _compute_text(
    metric_def: MetricDefinition,
    period: str,
    adapter: DataSourceAdapter,
    source_name: str,
) -> MetricFact:
    """text：mode=fixed 直接返回固定文案；mode=latest 取最新原始采样值。

    text 不做聚合、不做数值变换，也不参与基线对比（baseline/change 恒为 None）。
    """
    cfg = (metric_def.type_config or {}).get("text_config") or {}
    try:
        text_cfg = TextConfig.model_validate(cfg)
    except ValidationError as exc:
        raise MetricComputeError(f"type_config.text_config 非法: {exc}") from None

    try:
        # text 也要合法的 period：mode=latest 会用它取「周期末点」
        start, end = period_to_unix_range(period)
    except ValueError as exc:
        raise MetricComputeError(str(exc)) from exc

    items = await resolve_items(metric_def, adapter)
    qc = metric_def.query_config or {}
    group_names, host_names = await _resolve_scope_names(adapter, qc, items)

    if text_cfg.mode == "fixed":
        text_value = text_cfg.fixed_value or ""
        object_desc = _build_object_desc(group_names, items) if items else (
            f"{', '.join(group_names)}（固定文本，不绑定监控项）"
        )
        formula_desc = "固定文本（mode=fixed，不取数）"
    else:
        if len(items) != 1:
            raise MetricComputeError("text mode=latest 需要恰好一个监控项")
        # 传周期区间：AIOps 侧据此取「周期末点」而不是「调用时刻的当前值」，
        # 这样报告重跑结果稳定可复核；Zabbix 侧忽略该区间，行为与升级前一致。
        latest = await adapter.fetch_latest_value(
            items[0]["item_id"], start, end
        )
        if latest is None:
            raise MetricComputeError(f"监控项 {items[0]['item_id']} 无历史数据")
        text_value = str(latest.get("value", ""))
        object_desc = _build_object_desc(group_names, items)
        stamp = latest.get("clock")
        when = (
            datetime.fromtimestamp(int(stamp), tz=UTC).strftime("%Y-%m-%d %H:%M")
            if stamp
            else "未知时间"
        )
        formula_desc = (
            f"区间内最后一个原始值 @ {when}（mode=latest，不聚合；"
            f"周期 {period}）"
        )

    logger.info(
        "指标计算完成 name=%s type=text period=%s mode=%s",
        metric_def.name,
        period,
        text_cfg.mode,
    )
    return MetricFact(
        metric_id=metric_def.id,
        name=metric_def.name,
        value={"text": text_value},
        value_shape="text",
        baseline_value=None,
        change_pct=None,
        unit=metric_def.unit,
        source=source_name,
        object=object_desc,
        formula=formula_desc,
        period=period,
    )
