"""metric_type 分型测试（阶段 1：scalar 数值变换 + text）。

覆盖：
- Transform 组合（正/负/小数/负 mul / 恒等）
- scalar：value 与 baseline 同口径变换、change_pct 基于变换后数值
- text：mode=latest（history.get）/ mode=fixed（不取数）
- curve / pie：阶段 1 未实现，compute 显式报错
- 入参 discriminated union 拒绝错配
- curve/pie 指标禁止绑定模板（阶段 1-2 护栏）
"""

from typing import Any

import pytest
from datetime import datetime, timezone
from pydantic import TypeAdapter, ValidationError

from app.models.metric import MetricDefinition
from app.schemas.metric import (
    MetricCreate,
    MetricFact,
    PieConfig,
    TextConfig,
    Transform,
)
from app.services.adapters.base import DataSourceAdapter
from app.services.calc_engine import MetricComputeError, compute_metric
from app.services.period_utils import baseline_range, period_to_unix_range

def _ts(y: int, m: int) -> int:
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp())


SEP = _ts(2026, 9)
OCT = _ts(2026, 10)
AUG = _ts(2026, 8)
PERIOD = "2026-09"
# 区间 key 直接取自被测函数，避免手写天数与实现漂移
CUR_RANGE = period_to_unix_range(PERIOD)
BASE_RANGE = baseline_range(PERIOD, 1)


def _points(item_id: int, clock: int, value: float) -> list[dict[str, Any]]:
    return [
        {
            "item_id": item_id,
            "clock": clock,
            "num": 60,
            "value_min": value,
            "value_avg": value,
            "value_max": value,
        }
    ]


def _cur_trend(item_id: int, value: float) -> dict[tuple[int, int], list[dict]]:
    return {CUR_RANGE: _points(item_id, SEP + 3600, value)}


def _base_trend(item_id: int, value: float) -> dict[tuple[int, int], list[dict]]:
    return {BASE_RANGE: _points(item_id, AUG + 3600, value)}


class TypeAdapter_(DataSourceAdapter):
    """可控的 FakeAdapter：trend 与 latest 值均可注入。"""

    def __init__(
        self,
        trend_map: dict[tuple[int, int], list[dict[str, Any]]],
        latest: dict[str, Any] | None = None,
    ) -> None:
        super().__init__({}, None)
        self._trend = trend_map
        self._latest = latest
        self.latest_calls: list[int] = []

    async def test_connection(self) -> bool:
        return True

    async def list_host_groups(self):
        return [{"group_id": 4, "name": "Linux servers"}]

    async def list_hosts(self, group_ids):
        return [
            {
                "host_id": 1,
                "name": "host-1",
                "groups": [{"group_id": 4, "name": "Linux servers"}],
            }
        ]

    async def list_items(self, host_ids, value_types=None):
        return [
            {
                "item_id": 101,
                "name": "cpu",
                "key": "system.cpu.util",
                "host_id": 1,
                "host": "host-1",
                "value_type": 0,
                "units": "%",
            }
        ]

    async def fetch_latest_value(
        self, item_id: int, time_from: int | None = None, time_till: int | None = None
    ):
        self.latest_calls.append(item_id)
        return self._latest

    async def get_item_meta(self, item_id: int):
        return None
    async def fetch_trend_data(self, item_ids, time_from, time_till):
        return self._trend.get((time_from, time_till), [])


def _metric(
    metric_type: str = "scalar",
    type_config: dict[str, Any] | None = None,
    calc_formula: dict[str, Any] | None = None,
    query_config: dict[str, Any] | None = None,
    unit: str = "%",
    unit_config: dict[str, Any] | None = None,
    display_precision: int | None = None,
) -> MetricDefinition:
    return MetricDefinition(
        id=1,
        name="m1",
        data_source_id=1,
        metric_type=metric_type,
        query_config=query_config
        or {"host_group_ids": ["4"], "host_ids": ["1"], "item_ids": ["101"]},
        type_config=type_config if type_config is not None else {},
        calc_formula=calc_formula or {"agg": "avg", "compare": "none"},
        unit=unit,
        unit_config=unit_config,
        display_precision=display_precision,
        period_type="monthly",
    )


class TestTransformUnit:
    @pytest.mark.parametrize(
        ("raw", "mul", "add", "expected"),
        [
            (10.0, 1.0, 0.0, 10.0),  # 恒等
            (10.0, 0.001, 0.0, 0.01),  # bps -> Mbps
            (10.0, 2.0, 5.0, 25.0),  # 2x+5
            (10.0, 0.0, 7.5, 7.5),  # 常数
            (10.0, -1.0, 0.0, -10.0),  # 取反
            (-4.0, 0.5, 1.0, -1.0),  # 负值 + 负 mul
            (3.3333333, 3.0, 0.0, 9.9999999),  # 小数精度
        ],
    )
    def test_apply(self, raw, mul, add, expected) -> None:
        assert Transform(mul=mul, add=add).apply(raw) == pytest.approx(expected)

    def test_identity_flag(self) -> None:
        assert Transform().is_identity is True
        assert Transform(mul=1.0, add=0.0).is_identity is True
        assert Transform(mul=2.0).is_identity is False
        assert Transform(add=1.0).is_identity is False


class TestScalarTransform:
    async def test_value_transformed(self) -> None:
        adapter = TypeAdapter_(_cur_trend(101, 100.0))
        fact = await compute_metric(
            _metric(type_config={"transform": {"mul": 0.001, "add": 0}}),
            PERIOD,
            adapter,
            "ds",
        )
        assert fact.value_shape == "scalar"
        assert fact.value == pytest.approx(0.1)
        assert "×0.001" in fact.formula

    async def test_baseline_transformed_and_change_pct(self) -> None:
        """本期 100 -> 200；基线 50 -> 100，change_pct 基于变换后数值 = 100%。"""
        trend = {**_cur_trend(101, 100.0), **_base_trend(101, 50.0)}
        adapter = TypeAdapter_(trend)
        fact = await compute_metric(
            _metric(
                type_config={"transform": {"mul": 2.0, "add": 0}},
                calc_formula={"agg": "avg", "compare": "mom", "baseline_lookback": 1},
            ),
            PERIOD,
            adapter,
            "ds",
        )
        assert fact.value == pytest.approx(200.0)
        assert fact.baseline_value == pytest.approx(100.0)
        assert fact.change_pct == pytest.approx(100.0)

    async def test_identity_transform_no_formula_suffix(self) -> None:
        adapter = TypeAdapter_(_cur_trend(101, 42.0))
        fact = await compute_metric(_metric(), PERIOD, adapter, "ds")
        assert fact.value == pytest.approx(42.0)
        assert "×" not in fact.formula

    async def test_illegal_transform_rejected(self) -> None:
        m = _metric()
        m.type_config = {"transform": {"mul": "not-a-number"}}
        with pytest.raises(MetricComputeError, match="transform"):
            await compute_metric(m, PERIOD, TypeAdapter_({}), "ds")


class TestScalarUnitScale:
    """单位换算只改展示口径：事实层数值与 change_pct 必须逐位不变。"""

    async def test_auto_display_and_baseline_share_factor(self) -> None:
        trend = {**_cur_trend(101, 262468.9041666667), **_base_trend(101, 118000.0)}
        adapter = TypeAdapter_(trend)
        fact = await compute_metric(
            _metric(
                unit="bps",
                unit_config={"mode": "auto", "base": 1000, "suffix_unit": "bps"},
                calc_formula={"agg": "avg", "compare": "mom", "baseline_lookback": 1},
            ),
            PERIOD,
            adapter,
            "ds",
        )
        # 事实层原样保留（原始口径）
        assert fact.value == pytest.approx(262468.9041666667)
        assert fact.baseline_value == pytest.approx(118000.0)
        # 展示派生层：本期选 K 档，基线必须复用同一因子（否则环比失真）
        assert fact.display_factor == 1000
        assert fact.display_value == pytest.approx(262.4689041667)
        assert fact.display_baseline_value == pytest.approx(118.0)
        assert fact.display_unit == "Kbps"
        # 开启换算且指标未配精度 -> 默认补 2 位（不写回 display_precision 之外的行为）
        assert fact.display_precision == 2
        # change_pct 仍基于原始值：(262468.9-118000)/118000*100
        assert fact.change_pct == pytest.approx((262468.9041666667 - 118000.0) / 118000.0 * 100)
        assert "展示口径" in fact.formula
        assert "÷1000" in fact.formula

    async def test_change_pct_identical_with_and_without_config(self) -> None:
        """关键回归：加密换算前后，value / baseline / change_pct 必须完全一致。"""
        trend = {**_cur_trend(101, 262468.9041666667), **_base_trend(101, 118000.0)}
        calc = {"agg": "avg", "compare": "mom", "baseline_lookback": 1}
        raw = await compute_metric(_metric(unit="bps", calc_formula=calc), PERIOD, TypeAdapter_(trend), "ds")
        scaled = await compute_metric(
            _metric(
                unit="bps",
                unit_config={"mode": "auto", "suffix_unit": "bps"},
                calc_formula=calc,
            ),
            PERIOD,
            TypeAdapter_(trend),
            "ds",
        )
        assert scaled.value == raw.value
        assert scaled.baseline_value == raw.baseline_value
        assert scaled.change_pct == raw.change_pct

    async def test_no_config_keeps_display_fields_none(self) -> None:
        fact = await compute_metric(_metric(unit="bps"), PERIOD, TypeAdapter_(_cur_trend(101, 5.0)), "ds")
        assert fact.display_value is None
        assert fact.display_baseline_value is None
        assert fact.display_unit is None
        assert fact.display_factor is None
        assert fact.display_precision is None

    async def test_metric_precision_used_when_set(self) -> None:
        fact = await compute_metric(
            _metric(
                unit="bps",
                unit_config={"mode": "auto", "suffix_unit": "bps"},
                display_precision=4,
            ),
            PERIOD,
            TypeAdapter_(_cur_trend(101, 262468.9041666667)),
            "ds",
        )
        assert fact.display_precision == 4

    async def test_config_decimals_override_metric_precision(self) -> None:
        fact = await compute_metric(
            _metric(
                unit="bps",
                unit_config={"mode": "auto", "suffix_unit": "bps", "decimals": 1},
                display_precision=4,
            ),
            PERIOD,
            TypeAdapter_(_cur_trend(101, 262468.9041666667)),
            "ds",
        )
        assert fact.display_precision == 1

    async def test_fixed_divisor(self) -> None:
        fact = await compute_metric(
            _metric(
                unit="bps",
                unit_config={"mode": "fixed", "divisor": 1000, "display_unit": "Kbps"},
            ),
            PERIOD,
            TypeAdapter_(_cur_trend(101, 262468.9)),
            "ds",
        )
        assert fact.display_factor == 1000
        assert fact.display_value == pytest.approx(262.4689)
        assert fact.display_unit == "Kbps"

    async def test_none_mode_is_noop(self) -> None:
        fact = await compute_metric(
            _metric(unit="bps", unit_config={"mode": "none"}),
            PERIOD,
            TypeAdapter_(_cur_trend(101, 5.0)),
            "ds",
        )
        assert fact.display_value is None
        assert fact.display_factor is None


class TestCurveUnitScale:
    async def _curve_fact(self, points: list[list[float]], **kw: Any) -> MetricFact:
        """一条曲线、两个点，走真实 compute_metric（覆盖 curve 分支的换算）。"""
        qc = {
            "host_group_ids": ["4"],
            "host_ids": ["1"],
            "series": [{"item_id": "101", "display_name": "入向"}],
        }
        trend = {
            CUR_RANGE: [
                {"item_id": 101, "clock": SEP + 3600, "num": 60, "value_min": p, "value_avg": p, "value_max": p}
                for p in points
            ]
        }
        return await compute_metric(
            _metric(metric_type="curve", query_config=qc, unit="bps", **kw),
            PERIOD,
            TypeAdapter_(trend),
            "ds",
        )

    async def test_unified_factor_from_max_abs_point(self) -> None:
        """曲线取全部点的最大绝对值统一选档（否则同一张图里点与点不同档）。"""
        fact = await self._curve_fact(
            [900.0, 262468.9041666667],
            unit_config={"mode": "auto", "base": 1000, "suffix_unit": "bps"},
        )
        assert fact.display_factor == 1000
        assert fact.display_unit == "Kbps"
        # 曲线不产出单一展示值（由渲染层逐点缩放）
        assert fact.display_value is None
        assert fact.display_precision == 2

    async def test_small_values_stay_in_base_unit(self) -> None:
        fact = await self._curve_fact(
            [10.0, 999.0],
            unit_config={"mode": "auto", "suffix_unit": "bps"},
        )
        assert fact.display_factor == 1
        assert fact.display_unit == "bps"

    async def test_no_config_keeps_display_fields_none(self) -> None:
        fact = await self._curve_fact([10.0, 20.0])
        assert fact.display_factor is None
        assert fact.display_unit is None


class TestText:
    async def test_mode_fixed_no_datasource_call(self) -> None:
        adapter = TypeAdapter_({})
        fact = await compute_metric(
            _metric(
                metric_type="text",
                type_config={"text_config": {"mode": "fixed", "fixed_value": "本月无异常"}},
                query_config={"host_group_ids": ["4"], "host_ids": ["1"]},
            ),
            PERIOD,
            adapter,
            "ds",
        )
        assert fact.value_shape == "text"
        assert fact.value == {"text": "本月无异常"}
        assert fact.baseline_value is None and fact.change_pct is None
        assert adapter.latest_calls == []  # 未取数
        assert "固定文本" in fact.formula

    async def test_mode_latest_uses_history(self) -> None:
        adapter = TypeAdapter_({}, latest={"item_id": 101, "clock": SEP, "value": "0.85", "units": ""})
        fact = await compute_metric(
            _metric(
                metric_type="text",
                type_config={"text_config": {"mode": "latest"}},
            ),
            PERIOD,
            adapter,
            "ds",
        )
        assert fact.value == {"text": "0.85"}
        assert adapter.latest_calls == [101]
        # 2026-10-16 起 text 取「区间内最后一个原始值」（AIOps 周期末点语义），
        # formula 随之改写；断言意图是「走了 latest 取数路径而非固定文案」
        assert "mode=latest" in fact.formula

    async def test_mode_latest_without_data_raises(self) -> None:
        adapter = TypeAdapter_({}, latest=None)
        with pytest.raises(MetricComputeError, match="无历史数据"):
            await compute_metric(
                _metric(metric_type="text", type_config={"text_config": {"mode": "latest"}}),
                PERIOD,
                adapter,
                "ds",
            )

    def test_text_config_fixed_requires_value(self) -> None:
        with pytest.raises(ValidationError):
            TextConfig(mode="fixed")




class TestCreateUnionValidation:
    ta = TypeAdapter(MetricCreate)

    def _base(self) -> dict[str, Any]:
        return {
            "name": "m",
            "data_source_id": 1,
            "unit": "%",
            "period_type": "monthly",
            "display_precision": None,
            "tags": [],
        }

    def test_scalar_ok(self) -> None:
        m = self.ta.validate_python(
            {
                **self._base(),
                "metric_type": "scalar",
                "query_config": {
                    "host_group_ids": ["4"],
                    "host_ids": ["1"],
                    "item_ids": ["101"],
                },
                "type_config": {"transform": {"mul": 0.001}},
            }
        )
        assert m.metric_type == "scalar"
        assert m.type_config.transform.mul == 0.001

    def test_metric_type_required(self) -> None:
        with pytest.raises(ValidationError):
            self.ta.validate_python(
                {
                    **self._base(),
                    "query_config": {
                        "host_group_ids": ["4"],
                        "host_ids": ["1"],
                        "item_ids": ["101"],
                    },
                }
            )

    def test_pie_rejects_multiple_items(self) -> None:
        with pytest.raises(ValidationError, match="pie 只支持单个监控项"):
            self.ta.validate_python(
                {
                    **self._base(),
                    "metric_type": "pie",
                    "query_config": {
                        "host_group_ids": ["4"],
                        "host_ids": ["1"],
                        "item_ids": ["101", "102"],
                    },
                }
            )

    def test_pie_rejects_series_shape(self) -> None:
        """把 curve 的 series 形态塞给 pie 必须被拒（discriminator 生效）。"""
        with pytest.raises(ValidationError):
            self.ta.validate_python(
                {
                    **self._base(),
                    "metric_type": "pie",
                    "query_config": {
                        "host_group_ids": ["4"],
                        "host_ids": ["1"],
                        "series": [{"item_id": "1"}],
                    },
                }
            )

    def test_curve_requires_series(self) -> None:
        with pytest.raises(ValidationError, match="curve 至少需要一条曲线"):
            self.ta.validate_python(
                {
                    **self._base(),
                    "metric_type": "curve",
                    "query_config": {"host_group_ids": ["4"], "host_ids": ["1"]},
                }
            )

    def test_curve_rejects_duplicate_item(self) -> None:
        with pytest.raises(ValidationError, match="不能重复配置多条曲线"):
            self.ta.validate_python(
                {
                    **self._base(),
                    "metric_type": "curve",
                    "query_config": {
                        "host_group_ids": ["4"],
                        "host_ids": ["1"],
                        "series": [
                            {"item_id": "1", "display_name": "a"},
                            {"item_id": "1", "display_name": "b"},
                        ],
                    },
                }
            )

    def test_text_latest_requires_single_item(self) -> None:
        with pytest.raises(ValidationError, match="mode=latest"):
            self.ta.validate_python(
                {
                    **self._base(),
                    "metric_type": "text",
                    "query_config": {"host_group_ids": ["4"], "host_ids": ["1"]},
                    "type_config": {"text_config": {"mode": "latest"}},
                }
            )

    def test_text_fixed_allows_empty_items(self) -> None:
        m = self.ta.validate_python(
            {
                **self._base(),
                "metric_type": "text",
                "query_config": {"host_group_ids": ["4"], "host_ids": ["1"]},
                "type_config": {"text_config": {"mode": "fixed", "fixed_value": "hi"}},
            }
        )
        assert m.query_config.item_ids == []

    def test_scope_still_required(self) -> None:
        # 上级与下级必须成对：只给 host_group_ids 不给 host_ids 应直接拒绝
        with pytest.raises(ValidationError, match="上级与下级必须成对填写"):
            self.ta.validate_python(
                {
                    **self._base(),
                    "metric_type": "scalar",
                    "query_config": {"host_group_ids": ["4"], "item_ids": ["101"]},
                }
            )
        # 两组都没给也要拒绝
        with pytest.raises(ValidationError, match="取数范围为空"):
            self.ta.validate_python(
                {
                    **self._base(),
                    "metric_type": "scalar",
                    "query_config": {"item_ids": ["101"]},
                }
            )
        # AIOps 的模型 / 实例同样是合法范围
        ok = self.ta.validate_python(
            {
                **self._base(),
                "metric_type": "scalar",
                "query_config": {
                    "model_ids": ["22"],
                    "instance_ids": ["14009"],
                    "item_ids": ["10143"],
                },
            }
        )
        assert ok.query_config.model_ids == ["22"]
        assert ok.query_config.instance_ids == ["14009"]

    def test_unit_scale_allowed_for_scalar(self) -> None:
        m = self.ta.validate_python(
            {
                **self._base(),
                "metric_type": "scalar",
                "query_config": {
                    "host_group_ids": ["4"],
                    "host_ids": ["1"],
                    "item_ids": ["101"],
                },
                "unit_config": {"mode": "auto", "base": 1000, "suffix_unit": "bps"},
            }
        )
        assert m.unit_config.mode == "auto"
        assert m.unit_config.to_storage() == {
            "mode": "auto",
            "base": 1000,
            "suffix_unit": "bps",
        }

    def test_unit_scale_rejected_for_pie(self) -> None:
        """占比/文本/时间类指标开启换算必须显式 422（静默忽略难排查）。"""
        with pytest.raises(ValidationError, match="单位换算仅支持"):
            self.ta.validate_python(
                {
                    **self._base(),
                    "metric_type": "pie",
                    "query_config": {
                        "host_group_ids": ["4"],
                        "host_ids": ["1"],
                        "item_ids": ["101"],
                    },
                    "unit_config": {"mode": "auto"},
                }
            )

    def test_unit_scale_none_mode_allowed_for_text(self) -> None:
        """mode=none 等价于「未配置」，任何类型都放行。"""
        m = self.ta.validate_python(
            {
                **self._base(),
                "metric_type": "text",
                "query_config": {
                    "host_group_ids": ["4"],
                    "host_ids": ["1"],
                    "item_ids": ["101"],
                },
                "type_config": {"text_config": {"mode": "latest"}},
                "unit_config": {"mode": "none"},
            }
        )
        assert m.unit_config.mode == "none"
        assert m.unit_config.to_storage() is None


class TestPieConfigValidation:
    def test_overlap_rejected(self) -> None:
        with pytest.raises(ValidationError, match="不能重叠"):
            PieConfig(up_values=["1", "0"], down_values=["0"])

    def test_empty_side_rejected(self) -> None:
        with pytest.raises(ValidationError, match="up_values 不能为空"):
            PieConfig(up_values=[], down_values=["0"])


class TestMetricFactBackCompat:
    def test_old_fact_defaults_to_scalar(self) -> None:
        """历史 facts_json 没有 value_shape 时按 scalar 解析。"""
        f = MetricFact.model_validate(
            {
                "metric_id": 1,
                "name": "m",
                "value": 1.5,
                "unit": "%",
                "source": "ds",
                "object": "o",
                "formula": "f",
                "period": "2026-09",
            }
        )
        assert f.value_shape == "scalar"
        assert f.value == 1.5
        # 旧快照没有展示派生字段 -> 一律 None，消费端回退原始值（行为不变）
        assert f.display_value is None
        assert f.display_baseline_value is None
        assert f.display_unit is None
        assert f.display_factor is None

    def test_dict_value_kept(self) -> None:
        f = MetricFact.model_validate(
            {
                "metric_id": 1,
                "name": "m",
                "value": {"text": "abc"},
                "value_shape": "text",
                "unit": "",
                "source": "ds",
                "object": "o",
                "formula": "f",
                "period": "2026-09",
            }
        )
        assert f.value_shape == "text"
        assert f.value == {"text": "abc"}


class TestTextValueStructure:
    """text 类型的 MetricFact.value 原始结构必须是 dict {"text": str}。

    决策：value 为 float | dict，text 走 dict 分支（不偏离 union）；
    渲染层（前端 factValueText / 后端 build_context）负责取 payload.text。
    """

    async def test_fixed_value_is_dict_not_str(self) -> None:
        fact = await compute_metric(
            _metric(
                metric_type="text",
                type_config={"text_config": {"mode": "fixed", "fixed_value": "本月无异常"}},
                query_config={"host_group_ids": ["4"], "host_ids": ["1"]},
            ),
            PERIOD,
            TypeAdapter_({}),
            "ds",
        )
        assert isinstance(fact.value, dict), "text 的 value 必须是 dict，不能是裸字符串"
        assert fact.value == {"text": "本月无异常"}
        assert fact.value_shape == "text"
        # 不得出现被 JSON 字符串化的痕迹
        assert not isinstance(fact.value, str)

    async def test_latest_value_is_dict_not_str(self) -> None:
        fact = await compute_metric(
            _metric(
                metric_type="text",
                type_config={"text_config": {"mode": "latest"}},
            ),
            PERIOD,
            TypeAdapter_({}, latest={"item_id": 101, "clock": SEP, "value": "0.85", "units": ""}),
            "ds",
        )
        assert fact.value == {"text": "0.85"}
        assert fact.value_shape == "text"

    async def test_fact_json_serialisable(self) -> None:
        """落库到 report.facts_json 的结构可 JSON 序列化（reports 依赖这一点）。"""
        import json

        fact = await compute_metric(
            _metric(
                metric_type="text",
                type_config={"text_config": {"mode": "fixed", "fixed_value": "ok"}},
                query_config={"host_group_ids": ["4"], "host_ids": ["1"]},
            ),
            PERIOD,
            TypeAdapter_({}),
            "ds",
        )
        dumped = json.loads(fact.model_dump_json())
        assert dumped["value"] == {"text": "ok"}
        assert dumped["value_shape"] == "text"
