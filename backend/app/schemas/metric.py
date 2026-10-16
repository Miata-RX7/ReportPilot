"""指标相关 Pydantic v2 schema。

分类型要点（阶段 1）：
- metric_type 决定 query_config 形态与 calc_engine 分支：scalar / curve / pie / text
- 入参用 discriminated union（discriminator=metric_type）做精确校验
- calc_formula 只服务 scalar；其余配置放 type_config
- MetricFact.value 为 float | dict，由 value_shape 告知消费端如何解析
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.services.period_utils import PERIOD_PATTERN

MetricType = Literal["scalar", "curve", "pie", "text", "time"]
ValueShape = Literal["scalar", "series", "pie", "text"]


def _clean_ids(v: object) -> object:
    """去空白、丢空串，避免 [""] 这类脏值通过校验。"""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return v


def _normalize_tags(v: object) -> object:
    """标签归一：去空白、丢空项、去重（保持顺序）。"""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        out: list[str] = []
        for t in v:
            s = str(t).strip()
            if s and s not in out:
                out.append(s)
        return out
    return v


# --------------------------------------------------------------------------
# 数值变换
# --------------------------------------------------------------------------
class Transform(BaseModel):
    """线性变换：value = raw * mul + add（默认恒等）。"""

    mul: float = 1.0
    add: float = 0.0

    def apply(self, raw: float) -> float:
        return raw * self.mul + self.add

    @property
    def is_identity(self) -> bool:
        return self.mul == 1.0 and self.add == 0.0


# --------------------------------------------------------------------------
# 单位换算（展示派生层，仅 scalar / curve）
# --------------------------------------------------------------------------
class UnitScaleConfig(BaseModel):
    """单位换算配置：纯比例的「展示派生层」，事实层数值永不改写。

    与「数值变换」（``type_config.transform``）的分工，改代码前务必分清：

    - ``transform`` 做仿射变换（raw*mul+add），**参与事实层数值**，环比与显著波动
      阈值判定都基于它；
    - ``unit_config`` 只决定「怎么显示」——展示值 = 事实值 ÷ factor，不参与任何计算，
      因此 262468.9041666667 bps 可以安全地展示为 262.47 Kbps。

    三种模式：

    - ``none``：不换算（默认，与升级前行为完全一致）
    - ``auto``：按本期值量级自动选档 K/M/G/T，进制可选 1000 或 1024；
      ``suffix_unit`` 留空时回退指标 ``unit``（"bps" -> Kbps / Mbps / Gbps）
    - ``fixed``：固定除数 + 目标单位，覆盖 1000/1024 之外的系数
    """

    mode: Literal["none", "auto", "fixed"] = "none"
    base: Literal[1000, 1024] = 1000
    suffix_unit: str = Field(default="", max_length=16)
    divisor: float = Field(default=1000.0, gt=0, le=1e12)
    display_unit: str = Field(default="", max_length=32)
    decimals: int | None = Field(default=None, ge=0, le=10)

    @field_validator("suffix_unit", "display_unit", mode="before")
    @classmethod
    def _strip_unit_text(cls, v: object) -> object:
        return str(v).strip() if v is not None else ""

    def to_storage(self) -> dict[str, Any] | None:
        """落库形态：``mode=none`` 存 NULL（与「未配置」等价，保持 DB 语义干净）。

        只写「该模式真正需要」的字段 —— 刻意不用 ``model_dump(exclude_defaults=True)``：
        ``divisor`` 的默认值就是 1000，用它会把「固定除数 = 1000」这种最常见的配置
        当成默认值丢掉，读回时退化成「不换算」。
        """
        if self.mode == "none":
            return None
        data: dict[str, Any] = {"mode": self.mode}
        if self.mode == "auto":
            data["base"] = self.base
            if self.suffix_unit:
                data["suffix_unit"] = self.suffix_unit
        else:
            data["divisor"] = self.divisor
            if self.display_unit:
                data["display_unit"] = self.display_unit
        if self.decimals is not None:
            data["decimals"] = self.decimals
        return data


def coerce_unit_config(raw: Any) -> UnitScaleConfig | None:
    """宽容解析存量 ``unit_config``：非法 -> None（= 不换算）。

    只服务读路径与落库复校：写路径用 ``UnitScaleConfig`` 严格校验并 422；这里兜的是
    「手工改过 DB」或「旧版本写坏」的极端数据，避免一个脏配置让指标读不出来、
    也让「只改标签」这类更新不会被历史脏数据连带拦下。
    """
    if raw is None or isinstance(raw, UnitScaleConfig):
        return raw if isinstance(raw, UnitScaleConfig) else None
    try:
        return UnitScaleConfig.model_validate(raw)
    except ValidationError:
        return None


# --------------------------------------------------------------------------
# type_config（按 metric_type 解释）
# --------------------------------------------------------------------------
class ScalarTypeConfig(BaseModel):
    transform: Transform = Field(default_factory=Transform)


class CurveSeries(BaseModel):
    """曲线图的一条独立曲线。"""

    item_id: str = Field(min_length=1)
    display_name: str = Field(default="", max_length=128)
    transform: Transform = Field(default_factory=Transform)

    _clean_item = field_validator("item_id", mode="before")(
        lambda v: str(v).strip() if v is not None else ""
    )


class CurveTypeConfig(BaseModel):
    transform_default: Transform = Field(default_factory=Transform)
    max_series: int = Field(default=12, ge=1, le=50)


class PieConfig(BaseModel):
    """UP / DOWN 归类规则。"""

    up_values: list[str] = Field(default_factory=lambda: ["1"])
    down_values: list[str] = Field(default_factory=lambda: ["0"])
    # exclude：未命中的小时不计入分母；error：出现未命中值直接报错
    unknown_mode: Literal["exclude", "error"] = "exclude"

    _clean_up = field_validator("up_values", mode="before")(_clean_ids)
    _clean_down = field_validator("down_values", mode="before")(_clean_ids)

    @model_validator(mode="after")
    def _check_partition(self) -> "PieConfig":
        if not self.up_values:
            raise ValueError("pie_config.up_values 不能为空")
        if not self.down_values:
            raise ValueError("pie_config.down_values 不能为空")
        overlap = set(self.up_values) & set(self.down_values)
        if overlap:
            raise ValueError(f"up_values 与 down_values 不能重叠: {sorted(overlap)}")
        return self


class PieTypeConfig(BaseModel):
    pie_config: PieConfig = Field(default_factory=PieConfig)


# 时间格式可用的模块白名单：字段 + 字面量分隔符
TIME_FIELD_MODULES = ("年", "月", "日", "时", "分", "秒")
TIME_LITERAL_MODULES = ("空格", "-", "/", ":", ".")
TIME_MODULES = TIME_FIELD_MODULES + TIME_LITERAL_MODULES
# 格式片段数量上限（防御乱配超长格式）
TIME_MAX_MODULES = 20


# 快速偏移：按**计算时刻**解析为具体日期（不是固定天数偏移，
# 否则报告重新生成时会算错）
TIME_QUICK_OFFSETS = (
    "last_week_monday",
    "last_week_sunday",
    "last_month_first",
    "last_month_end",
)
TIME_QUICK_OFFSET_LABELS = {
    "last_week_monday": "上周周一",
    "last_week_sunday": "上周周末",
    "last_month_first": "上月一号",
    "last_month_end": "上月月末",
}


class TimeConfig(BaseModel):
    """时间类型的计算配置：基准时间 = 当前时间，按偏移平移，再按 modules 格式化。

    format_modules 是**有序**的模块列表，同一个模块可重复（如 [年, 年]）。
    允许的模块见 TIME_MODULES：字段 年/月/日/时/分/秒，字面量 空格/-/ /:/:/.

    偏移二选一：
    - offset_mode="manual"：用 offset_value + offset_unit（+N 天 / +N 月）
    - offset_mode="quick"  ：用 quick_offset（上周一/上周末/上月一号/上月月末）
    """

    format_modules: list[str] = Field(
        default_factory=lambda: ["年", "-", "月"], max_length=TIME_MAX_MODULES
    )
    # 月/日/时/分/秒是否补零（年始终 4 位）。关闭后 2026-09 -> 2026-9
    zero_pad: bool = True
    offset_mode: Literal["manual", "quick"] = "manual"
    # manual 模式：平移量，正数向后、负数向前（如 -1 表示往前一个月）
    offset_value: int = 0
    offset_unit: Literal["day", "month"] = "day"
    # quick 模式：按计算时刻解析的具体日期
    quick_offset: Literal[
        "last_week_monday", "last_week_sunday", "last_month_first", "last_month_end"
    ] | None = None

    @field_validator("format_modules", mode="before")
    @classmethod
    def _clean_modules(cls, v: object) -> object:
        if not isinstance(v, (list, tuple)):
            raise ValueError("format_modules 必须是列表")
        out: list[str] = []
        for item in v:
            m = str(item)
            if m not in TIME_MODULES:
                raise ValueError(
                    f"不支持的时间模块 {m!r}，可用：{' '.join(TIME_MODULES)}"
                )
            out.append(m)
        if not out:
            raise ValueError("format_modules 不能为空（否则时间指标没有输出）")
        return out

    @model_validator(mode="after")
    def _check_offset(self) -> "TimeConfig":
        if self.offset_mode == "quick":
            if self.quick_offset not in TIME_QUICK_OFFSETS:
                raise ValueError(
                    "offset_mode=quick 时 quick_offset 必填，可选："
                    + " / ".join(TIME_QUICK_OFFSETS)
                )
        elif abs(self.offset_value) > 1000:
            raise ValueError("offset_value 绝对值不能超过 1000")
        return self

    def label(self) -> str:
        """偏移量的中文描述（供 formula 字段与前端摘要复用）。"""
        if self.offset_mode == "quick":
            return TIME_QUICK_OFFSET_LABELS.get(self.quick_offset or "", "快速偏移")
        if self.offset_value == 0:
            return "不偏移"
        unit = "天" if self.offset_unit == "day" else "月"
        return f"{'+' if self.offset_value > 0 else ''}{self.offset_value}{unit}"


class TimeTypeConfig(BaseModel):
    time_config: TimeConfig = Field(default_factory=TimeConfig)


class TextConfig(BaseModel):
    """文本取值方式：最新值 or 固定文案。"""

    mode: Literal["latest", "fixed"] = "latest"
    fixed_value: str | None = None

    @model_validator(mode="after")
    def _check_fixed(self) -> "TextConfig":
        if self.mode == "fixed" and self.fixed_value is None:
            raise ValueError("mode=fixed 时 fixed_value 必填")
        return self


class TextTypeConfig(BaseModel):
    text_config: TextConfig = Field(default_factory=TextConfig)


# --------------------------------------------------------------------------
# query_config（分类型）
# --------------------------------------------------------------------------
class _ScopeBase(BaseModel):
    """取数范围：Zabbix 是「主机组 / 主机」，AIOps 是「模型 / 实例」。

    两组字段都放在同一个基类里，各取所需、互不混用。Pydantic 层**只做结构性
    检查**（至少一组非空），「这个数据源必须填哪一组」由 service 层按
    ``DataSource.type`` 强校验 —— schema 拿不到数据源类型，硬判会写死。
    """

    # Zabbix
    host_group_ids: list[str] = Field(default_factory=list)
    host_ids: list[str] = Field(default_factory=list)
    # AIOps（语义：模型 / 实例）
    model_ids: list[str] = Field(default_factory=list)
    instance_ids: list[str] = Field(default_factory=list)

    _clean_groups = field_validator(
        "host_group_ids", "model_ids", mode="before"
    )(_clean_ids)
    _clean_hosts = field_validator(
        "host_ids", "instance_ids", mode="before"
    )(_clean_ids)

    @model_validator(mode="after")
    def _check_scope(self) -> "_ScopeBase":
        """至少一组「上级 + 下级」都填了。

        只要填了其中一半就算配置错误：半截范围会在取数时才暴露成一句
        难以定位的报错（Zabbix 报「主机不在主机组里」，AIOps 报「实例不在模型里」），
        不如在保存时就拦下。
        """
        pairs = (
            ("host_group_ids", "host_ids", "Zabbix 主机组/主机"),
            ("model_ids", "instance_ids", "AIOps 模型/实例"),
        )
        complete = [
            label
            for upper, lower, label in pairs
            if getattr(self, upper) and getattr(self, lower)
        ]
        partial = [
            label
            for upper, lower, label in pairs
            if bool(getattr(self, upper)) != bool(getattr(self, lower))
        ]
        if partial:
            raise ValueError(
                f"取数范围：{'、'.join(partial)} 上级与下级必须成对填写"
            )
        if not complete:
            raise ValueError("取数范围为空：请选择主机组与主机，或模型与实例")
        return self


class ScalarQueryConfig(_ScopeBase):
    """scalar：多 item 聚合为一个数值。"""

    item_ids: list[str] = Field(default_factory=list)
    value_type_override: list[int] | None = None

    _clean_items = field_validator("item_ids", mode="before")(_clean_ids)

    @model_validator(mode="after")
    def _check_items(self) -> "ScalarQueryConfig":
        if not self.item_ids:
            raise ValueError("scalar 至少需要一个监控项（item_ids）")
        return self


class CurveQueryConfig(_ScopeBase):
    """curve：每条 series 独立成一条曲线。"""

    series: list[CurveSeries] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_series(self) -> "CurveQueryConfig":
        if not self.series:
            raise ValueError("curve 至少需要一条曲线（series）")
        ids = [s.item_id for s in self.series]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"同一监控项不能重复配置多条曲线: {dup}")
        return self


class PieQueryConfig(_ScopeBase):
    """pie：严格单 item。"""

    item_ids: list[str] = Field(default_factory=list)
    value_type_override: list[int] | None = None

    _clean_items = field_validator("item_ids", mode="before")(_clean_ids)

    @model_validator(mode="after")
    def _check_single(self) -> "PieQueryConfig":
        if len(self.item_ids) != 1:
            raise ValueError("pie 只支持单个监控项（item_ids 长度必须为 1）")
        return self


class TextQueryConfig(_ScopeBase):
    """text：mode=latest 需单 item；mode=fixed 时可为空。"""

    item_ids: list[str] = Field(default_factory=list)
    value_type_override: list[int] | None = None

    _clean_items = field_validator("item_ids", mode="before")(_clean_ids)

    @model_validator(mode="after")
    def _check_single(self) -> "TextQueryConfig":
        if len(self.item_ids) > 1:
            raise ValueError("text 最多支持单个监控项（item_ids 长度 <= 1）")
        return self


class TimeQueryConfig(BaseModel):
    """时间类型不取数，query_config 为空对象。

    刻意不继承 _ScopeBase：时间指标与数据源无关，不该被主机组/主机/监控项
    的必填校验拦住。
    """

    model_config = ConfigDict(extra="allow")


class CalcFormula(BaseModel):
    """**仅 scalar 使用**的聚合与对比配置。"""

    agg: Literal["sum", "avg", "max", "min"] = "avg"
    compare: Literal["none", "mom", "yoy"] = "none"
    baseline_lookback: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check_lookback(self) -> "CalcFormula":
        if self.compare != "none" and self.baseline_lookback is None:
            raise ValueError("compare 非 none 时 baseline_lookback 必填且 >= 1")
        return self


# --------------------------------------------------------------------------
# 读接口：query_config 保持宽松（按 metric_type 形态不同）
# --------------------------------------------------------------------------
class QueryConfig(BaseModel):
    """读取用宽松结构：scalar/pie/text 用 item_ids，curve 用 series。

    取数范围两组都在（Zabbix 主机组/主机、AIOps 模型/实例），前端按数据源类型取用。
    """

    model_config = ConfigDict(extra="allow")

    host_group_ids: list[str] = Field(default_factory=list)
    host_ids: list[str] = Field(default_factory=list)
    model_ids: list[str] = Field(default_factory=list)
    instance_ids: list[str] = Field(default_factory=list)
    item_ids: list[str] = Field(default_factory=list)
    series: list[dict[str, Any]] = Field(default_factory=list)
    value_type_override: list[int] | None = None


# --------------------------------------------------------------------------
# 入参：按 metric_type 的 discriminated union
# --------------------------------------------------------------------------
class _MetricCommon(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    # time 类型不取数 -> 不需要数据源；其余类型必填（见 _require_datasource）
    data_source_id: int | None = None
    unit: str = Field(default="", max_length=32)
    period_type: Literal["monthly", "weekly", "daily"] = "monthly"
    display_precision: int | None = Field(default=None, ge=0, le=10)
    tags: list[str] = Field(default_factory=list)
    unit_config: UnitScaleConfig | None = None

    _clean_tags = field_validator("tags", mode="before")(_normalize_tags)

    @model_validator(mode="after")
    def _require_datasource_except_time(self) -> "_MetricCommon":
        """除 time 外都必须指定数据源。

        metric_type 由子类声明（它是 discriminated union 的 discriminator），
        运行期 self 已是子类实例，故此处可读；用 getattr 兜底避免基类被直接实例化时炸掉。
        """
        if getattr(self, "metric_type", None) != "time" and self.data_source_id is None:
            raise ValueError("data_source_id 必填（仅 time 类型可不指定数据源）")
        return self

    @model_validator(mode="after")
    def _check_unit_scale_scope(self) -> "_MetricCommon":
        """单位换算只服务 scalar / curve（连续量）；pie/text/time 配置即拒绝。

        静默忽略会让页面上「配了却不生效」难以排查，所以宁可显式 422。
        """
        if (
            self.unit_config is not None
            and self.unit_config.mode != "none"
            and getattr(self, "metric_type", None) not in ("scalar", "curve")
        ):
            raise ValueError(
                "单位换算仅支持 scalar / curve 类型指标"
                f"（当前 metric_type={getattr(self, 'metric_type', None)}）"
            )
        return self


class ScalarMetricCreate(_MetricCommon):
    metric_type: Literal["scalar"]
    query_config: ScalarQueryConfig
    type_config: ScalarTypeConfig = Field(default_factory=ScalarTypeConfig)
    calc_formula: CalcFormula = Field(default_factory=CalcFormula)


class CurveMetricCreate(_MetricCommon):
    metric_type: Literal["curve"]
    query_config: CurveQueryConfig
    type_config: CurveTypeConfig = Field(default_factory=CurveTypeConfig)


class PieMetricCreate(_MetricCommon):
    metric_type: Literal["pie"]
    query_config: PieQueryConfig
    type_config: PieTypeConfig = Field(default_factory=PieTypeConfig)

    @model_validator(mode="after")
    def _check_pie_fields(self) -> "PieMetricCreate":
        return self


class TextMetricCreate(_MetricCommon):
    metric_type: Literal["text"]
    query_config: TextQueryConfig
    type_config: TextTypeConfig = Field(default_factory=TextTypeConfig)

    @model_validator(mode="after")
    def _check_text_mode(self) -> "TextMetricCreate":
        """mode=fixed 不需要监控项；mode=latest 必须恰好一个。"""
        mode = self.type_config.text_config.mode
        n = len(self.query_config.item_ids)
        if mode == "latest" and n != 1:
            raise ValueError("text mode=latest 时 item_ids 长度必须为 1")
        return self


class TimeMetricCreate(_MetricCommon):
    metric_type: Literal["time"]
    query_config: TimeQueryConfig = Field(default_factory=TimeQueryConfig)
    type_config: TimeTypeConfig = Field(default_factory=TimeTypeConfig)

    @model_validator(mode="after")
    def _no_datasource_needed(self) -> "TimeMetricCreate":
        return self


def _inject_metric_type(v: object) -> object:
    """未传 metric_type 时默认 scalar（保持旧客户端与旧数据可写）。"""
    if isinstance(v, dict) and not v.get("metric_type"):
        return {**v, "metric_type": "scalar"}
    return v


# 入参：metric_type 必填的 discriminated union（按类型精确校验 query_config/type_config）
MetricCreate = Annotated[
    Union[
        ScalarMetricCreate,
        CurveMetricCreate,
        PieMetricCreate,
        TextMetricCreate,
        TimeMetricCreate,
    ],
    Field(discriminator="metric_type"),
]


class MetricUpdate(BaseModel):
    """更新：全量可选。跨字段（类型/配置）一致性在 service 层按需校验。"""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    data_source_id: int | None = None
    metric_type: MetricType | None = None
    query_config: dict[str, Any] | None = None
    type_config: dict[str, Any] | None = None
    calc_formula: dict[str, Any] | None = None
    unit: str | None = Field(default=None, max_length=32)
    period_type: Literal["monthly", "weekly", "daily"] | None = None
    display_precision: int | None = Field(default=None, ge=0, le=10)
    tags: list[str] | None = None
    # 传 {"mode": "none"} 关闭换算（None = 本次不修改，与其它字段语义一致）
    unit_config: UnitScaleConfig | None = None

    _clean_tags = field_validator("tags", mode="before")(_normalize_tags)


class MetricDefinitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    # time 类型为 null（不取数）
    data_source_id: int | None
    metric_type: MetricType = "scalar"
    query_config: QueryConfig
    type_config: dict[str, Any] = Field(default_factory=dict)
    calc_formula: CalcFormula
    unit: str
    period_type: str
    display_precision: int | None
    tags: list[str]
    is_active: bool
    created_at: datetime
    # NULL / {"mode": "none"} = 不换算
    unit_config: UnitScaleConfig | None = None

    @field_validator("unit_config", mode="before")
    @classmethod
    def _tolerant_unit_config(cls, v: object) -> object:
        """读路径容错：非法存量配置退化为 None（= 不换算），不拖垮整个指标读取。"""
        return coerce_unit_config(v)


class ResolvedGroup(BaseModel):
    id: str
    name: str


class ResolvedHost(BaseModel):
    id: str
    name: str


class ResolvedItem(BaseModel):
    id: str
    name: str
    host_id: str
    host: str


class QueryConfigResolved(BaseModel):
    """query_config 中 id 的名称解析结果，供前端回填下拉展示。"""

    host_groups: list[ResolvedGroup] = Field(default_factory=list)
    hosts: list[ResolvedHost] = Field(default_factory=list)
    items: list[ResolvedItem] = Field(default_factory=list)
    # curve 的 series 名称解析（item_id -> 名称），与 items 同源
    series: list[ResolvedItem] = Field(default_factory=list)


class MetricListItem(MetricDefinitionRead):
    """列表项：附带 id -> name 解析结果（数据源不可达时为 null）。"""

    query_config_resolved: QueryConfigResolved | None = None


class MetricDetail(MetricDefinitionRead):
    query_config_resolved: QueryConfigResolved | None = None


class PieSuitability(BaseModel):
    """pie 适用性判定结果。

    - suitable=true：适合做 UP/DOWN 占比（confidence high/medium）
    - suitable=false：不适合（confidence low），前端可提示但允许用户强选
    - suitable="unknown"：数据量过大/超时，需人工确认（不硬阻断）
    """

    suitable: bool | Literal["unknown"]
    confidence: Literal["high", "medium", "low", "unknown"]
    reason: str
    distinct_values: list[str] = Field(default_factory=list)
    suggested_config: dict[str, Any] | None = None
    has_valuemap: bool = False
    valuemap: dict[str, str] = Field(default_factory=dict)


class ComputeRequest(BaseModel):
    period: str = Field(pattern=PERIOD_PATTERN)


class MetricFact(BaseModel):
    """指标计算结果（facts）。

    value 形态由 value_shape 决定：
    - scalar: float，且经过 transform（raw*mul+add）
    - series: {"series": [{"name": str, "points": [[ts, value], ...]}]}
    - pie:    {"up_count": int, "down_count": int, "up_pct": float, "down_pct": float}
    - text:   {"text": str}（时间类型 time 也复用此形态）
    展示格式化由渲染层负责。
    """

    metric_id: int
    name: str
    value: float | dict[str, Any]
    value_shape: ValueShape = "scalar"
    baseline_value: float | None = None
    change_pct: float | None = None  # 百分数：-5.0 即下降 5%
    unit: str
    source: str  # DataSource.name
    object: str  # 人类可读的原始对象描述
    formula: str  # 人类可读的实际计算口径
    period: str
    # 该指标的小数精度（来自 MetricDefinition.display_precision）；
    # 前端按此格式化值。None 表示后端未记录（旧报告），前端回退原始值。
    # 开启换算且指标未配精度时，calc_engine 会把它补成默认展示位数（2 位）。
    display_precision: int | None = None
    # ---- 展示派生层（由 MetricDefinition.unit_config 决定）----
    # 事实层字段（value / baseline_value / change_pct）永远是原始口径，换算只改展示；
    # None = 未配置换算（或旧报告快照），消费端一律回退上方字段。
    display_value: float | None = None
    display_baseline_value: float | None = None
    display_unit: str | None = None
    # 实际生效的除数（供图表逐点缩放；未换算为 None）
    display_factor: float | None = None
