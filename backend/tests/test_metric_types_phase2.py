"""阶段 2 测试：curve 多序列、pie 占比口径与精度、pie 适用性判定。"""

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from app.models.metric import MetricDefinition
from app.services import pie_suitability
from app.services.adapters.base import DataSourceAdapter
from app.services.calc_engine import MetricComputeError, compute_metric
from app.services.period_utils import period_to_unix_range

PERIOD = "2026-09"
CUR = period_to_unix_range(PERIOD)


def _row(item_id: int, clock: int, avg: float, mx: float | None = None) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "clock": clock,
        "num": 60,
        "value_min": avg,
        "value_avg": avg,
        "value_max": mx if mx is not None else avg,
    }


class FakeAdapter(DataSourceAdapter):
    def __init__(
        self,
        trend: list[dict[str, Any]] | None = None,
        meta: dict[str, Any] | None = None,
        delay: float = 0.0,
        missing: bool = False,
    ) -> None:
        super().__init__({}, None)
        self._trend = trend or []
        self._meta = meta
        self._delay = delay
        self._missing = missing
        self.fetch_calls: list[tuple[list[int], int, int]] = []

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
                "item_id": i,
                "name": f"item-{i}",
                "key": f"key.{i}",
                "host_id": 1,
                "host": "host-1",
                "value_type": 0,
                "units": "",
            }
            for i in (101, 102, 103)
        ]

    async def fetch_latest_value(
        self, item_id: int, time_from: int | None = None, time_till: int | None = None
    ):
        return None

    async def get_item_meta(self, item_id: int):
        if self._missing:
            return None
        if self._meta is None:
            # 默认给一个数值型 item（用例可显式传 None 测"item 不存在"）
            return {
                "item_id": item_id,
                "name": f"item-{item_id}",
                "key": "key.1",
                "value_type": 0,
                "units": "",
                "valuemap": {},
            }
        return self._meta

    async def fetch_trend_data(self, item_ids, time_from, time_till):
        if self._delay:
            await asyncio.sleep(self._delay)
        self.fetch_calls.append((list(item_ids), time_from, time_till))
        return [r for r in self._trend if r["item_id"] in set(item_ids)]


def _metric(
    metric_type: str,
    query_config: dict[str, Any],
    type_config: dict[str, Any] | None = None,
    display_precision: int | None = None,
) -> MetricDefinition:
    return MetricDefinition(
        id=1,
        name="m",
        data_source_id=1,
        metric_type=metric_type,
        query_config=query_config,
        type_config=type_config or {},
        calc_formula={"agg": "avg", "compare": "none"},
        unit="",
        period_type="monthly",
        display_precision=display_precision,
    )


H = CUR[0]


class TestCurve:
    def _metric_2series(self) -> MetricDefinition:
        return _metric(
            "curve",
            {
                "host_group_ids": ["4"],
                "host_ids": ["1"],
                "series": [
                    {"item_id": "101", "display_name": "入向", "transform": {"mul": 2.0, "add": 1.0}},
                    {"item_id": "102", "display_name": "出向"},
                ],
            },
        )

    def _trend(self) -> list[dict[str, Any]]:
        return [
            # 故意乱序，验证输出按 ts 升序
            _row(101, H + 7200, 20.0),
            _row(101, H + 3600, 10.0),
            _row(102, H + 3600, 5.0),
            _row(102, H + 7200, 7.0),
        ]

    async def test_two_series_single_rpc(self) -> None:
        """2 条曲线只发 1 次 trend.get。"""
        adapter = FakeAdapter(self._trend())
        await compute_metric(self._metric_2series(), PERIOD, adapter, "ds")
        assert len(adapter.fetch_calls) == 1
        assert sorted(adapter.fetch_calls[0][0]) == [101, 102]

    async def test_points_sorted_and_transformed(self) -> None:
        fact = await compute_metric(self._metric_2series(), PERIOD, FakeAdapter(self._trend()), "ds")
        assert fact.value_shape == "series"
        series = fact.value["series"]
        assert [s["name"] for s in series] == ["入向", "出向"]
        # 入向: (10*2+1, 20*2+1) 且按 ts 升序
        assert series[0]["points"] == [[H + 3600, 21.0], [H + 7200, 41.0]]
        # 出向: 恒等变换
        assert series[1]["points"] == [[H + 3600, 5.0], [H + 7200, 7.0]]

    async def test_missing_hours_not_padded(self) -> None:
        """缺失小时不补点（不插 null）。"""
        trend = [_row(101, H + 3600, 1.0), _row(101, H + 10800, 3.0)]
        m = _metric(
            "curve",
            {"host_group_ids": ["4"], "host_ids": ["1"], "series": [{"item_id": "101"}]},
        )
        fact = await compute_metric(m, PERIOD, FakeAdapter(trend), "ds")
        assert len(fact.value["series"][0]["points"]) == 2

    async def test_empty_points_allowed(self) -> None:
        """某条曲线无数据时 points 为空数组，不报错。"""
        trend = [_row(101, H + 3600, 1.0)]
        m = _metric(
            "curve",
            {
                "host_group_ids": ["4"],
                "host_ids": ["1"],
                "series": [{"item_id": "101"}, {"item_id": "102"}],
            },
        )
        fact = await compute_metric(m, PERIOD, FakeAdapter(trend), "ds")
        assert fact.value["series"][0]["points"] != []
        assert fact.value["series"][1]["points"] == []

    async def test_baseline_and_change_are_none(self) -> None:
        fact = await compute_metric(self._metric_2series(), PERIOD, FakeAdapter(self._trend()), "ds")
        assert fact.baseline_value is None and fact.change_pct is None

    async def test_max_series_limit(self) -> None:
        series = [{"item_id": str(101 + i)} for i in range(4)]  # 4 条 > max_series=3
        m = _metric(
            "curve",
            {"host_group_ids": ["4"], "host_ids": ["1"], "series": series},
            {"max_series": 3},
        )
        with pytest.raises(MetricComputeError, match="超过上限"):
            await compute_metric(m, PERIOD, FakeAdapter([]), "ds")

    async def test_empty_series_rejected(self) -> None:
        m = _metric("curve", {"host_group_ids": ["4"], "host_ids": ["1"], "series": []})
        with pytest.raises(MetricComputeError, match="至少需要一条曲线"):
            await compute_metric(m, PERIOD, FakeAdapter([]), "ds")

    async def test_no_trend_data_raises(self) -> None:
        with pytest.raises(MetricComputeError, match="无趋势数据"):
            await compute_metric(self._metric_2series(), PERIOD, FakeAdapter([]), "ds")

    async def test_duplicate_series_item_rejected(self) -> None:
        """重复 item 在 compute 的 series 解析阶段被拦下。"""
        m = _metric(
            "curve",
            {
                "host_group_ids": ["4"],
                "host_ids": ["1"],
                "series": [{"item_id": "101"}, {"item_id": "101"}],
            },
        )
        with pytest.raises(MetricComputeError, match="不能重复配置多条曲线"):
            await compute_metric(m, PERIOD, FakeAdapter([]), "ds")

    async def test_unknown_type_rejected(self) -> None:
        m = _metric("scalar", {"host_group_ids": ["4"], "host_ids": ["1"], "item_ids": ["101"]})
        m.metric_type = "line"
        with pytest.raises(MetricComputeError, match="未知的 metric_type"):
            await compute_metric(m, PERIOD, FakeAdapter([]), "ds")


class TestPie:
    def _metric(self, up, down, mode="exclude", precision=None) -> MetricDefinition:
        return _metric(
            "pie",
            {"host_group_ids": ["4"], "host_ids": ["1"], "item_ids": ["101"]},
            {
                "pie_config": {
                    "up_values": up,
                    "down_values": down,
                    "unknown_mode": mode,
                }
            },
            display_precision=precision,
        )

    async def test_value_max_mixed_hour_counts_up(self) -> None:
        """小时内既有 0 又有 1（avg=0.5）时，用 value_max 判为 UP。"""
        trend = [
            _row(101, H + 3600, 0.5, mx=1.0),  # 混合 -> UP
            _row(101, H + 7200, 0.0, mx=0.0),  # DOWN
        ]
        fact = await compute_metric(self._metric(["1"], ["0"]), PERIOD, FakeAdapter(trend), "ds")
        assert fact.value_shape == "pie"
        assert fact.value["up_count"] == 1
        assert fact.value["down_count"] == 1
        assert fact.value["up_pct"] == 50.0
        assert fact.value["down_pct"] == 50.0

    async def test_unknown_excluded_from_denominator(self) -> None:
        trend = [
            _row(101, H + 3600, 1.0),
            _row(101, H + 7200, 0.0),
            _row(101, H + 10800, 7.0),  # 未归类
        ]
        fact = await compute_metric(self._metric(["1"], ["0"]), PERIOD, FakeAdapter(trend), "ds")
        # 分母 = 2（不含未归类）
        assert fact.value["up_pct"] == 50.0
        assert fact.value["down_pct"] == 50.0
        assert "未归类 1" in fact.formula

    async def test_unknown_error_mode_raises(self) -> None:
        trend = [_row(101, H + 3600, 1.0), _row(101, H + 7200, 9.0)]
        with pytest.raises(MetricComputeError, match="未归类"):
            await compute_metric(
                self._metric(["1"], ["0"], mode="error"), PERIOD, FakeAdapter(trend), "ds"
            )

    async def test_pct_precision_default_one_decimal(self) -> None:
        trend = [_row(101, H + 3600, 1.0)] + [
            _row(101, H + 7200 + i * 3600, 0.0) for i in range(2)
        ]
        fact = await compute_metric(self._metric(["1"], ["0"]), PERIOD, FakeAdapter(trend), "ds")
        # 1/3 = 33.333... -> 1 位
        assert fact.value["up_pct"] == 33.3
        assert fact.value["down_pct"] == 66.7

    async def test_pct_precision_follows_display_precision(self) -> None:
        trend = [_row(101, H + 3600, 1.0)] + [
            _row(101, H + 7200 + i * 3600, 0.0) for i in range(2)
        ]
        fact = await compute_metric(
            self._metric(["1"], ["0"], precision=4), PERIOD, FakeAdapter(trend), "ds"
        )
        assert fact.value["up_pct"] == 33.3333
        assert fact.value["down_pct"] == 66.6667

    async def test_zero_total_gives_zero(self) -> None:
        m = _metric("pie", {"host_group_ids": ["4"], "host_ids": ["1"], "item_ids": ["101"]})
        trend = [_row(101, H + 3600, 0.0)]
        fact = await compute_metric(
            m, PERIOD, FakeAdapter(trend), "ds"
        ) if False else None
        # 全部未归类 -> 分母 0
        from app.schemas.metric import PieConfig

        cfg = PieConfig(up_values=["9"], down_values=["8"])
        m2 = _metric(
            "pie",
            {"host_group_ids": ["4"], "host_ids": ["1"], "item_ids": ["101"]},
            {"pie_config": {"up_values": ["9"], "down_values": ["8"]}},
        )
        fact = await compute_metric(m2, PERIOD, FakeAdapter(trend), "ds")
        assert fact.value["up_pct"] == 0.0 and fact.value["down_pct"] == 0.0

    async def test_no_trend_raises(self) -> None:
        with pytest.raises(MetricComputeError, match="无趋势数据"):
            await compute_metric(self._metric(["1"], ["0"]), PERIOD, FakeAdapter([]), "ds")


class TestPieSuitability:
    def _adapter(self, distinct_max, meta=None, delay=0.0) -> FakeAdapter:
        trend = [
            _row(101, H + i * 3600, float(v), mx=float(v)) for i, v in enumerate(distinct_max)
        ]
        return FakeAdapter(trend, meta=meta, delay=delay)

    async def test_two_values_high_confidence(self) -> None:
        a = self._adapter([0, 1, 0, 1])
        r = await pie_suitability.analyze_pie_suitability(a, 101)
        assert r.suitable is True
        assert r.confidence == "high"
        assert r.suggested_config == {"up_values": ["1"], "down_values": ["0"]}
        assert r.distinct_values == ["0", "1"]

    async def test_three_to_five_values_medium(self) -> None:
        r = await pie_suitability.analyze_pie_suitability(self._adapter([0, 1, 2]), 101)
        assert r.suitable is True
        assert r.confidence == "medium"
        assert r.suggested_config is None  # 让用户自己确认

    async def test_more_than_five_values_not_suitable(self) -> None:
        r = await pie_suitability.analyze_pie_suitability(
            self._adapter([0, 1, 2, 3, 4, 5]), 101
        )
        assert r.suitable is False
        assert r.confidence == "low"
        assert "状态过多" in r.reason

    async def test_single_value_not_suitable(self) -> None:
        r = await pie_suitability.analyze_pie_suitability(self._adapter([1, 1, 1]), 101)
        assert r.suitable is False
        assert "单一值" in r.reason

    async def test_non_numeric_not_suitable(self) -> None:
        meta = {"item_id": 101, "name": "x", "key": "k", "value_type": 2, "units": "", "valuemap": {}}
        r = await pie_suitability.analyze_pie_suitability(self._adapter([0, 1], meta=meta), 101)
        assert r.suitable is False
        assert "非数值型" in r.reason

    async def test_valuemap_detected(self) -> None:
        meta = {
            "item_id": 101,
            "name": "ICMP ping",
            "key": "icmpping",
            "value_type": 3,
            "units": "",
            "valuemap": {"0": "Down", "1": "Up"},
        }
        r = await pie_suitability.analyze_pie_suitability(self._adapter([0, 1], meta=meta), 101)
        assert r.has_valuemap is True
        assert r.valuemap == {"0": "Down", "1": "Up"}
        assert r.suitable is True
        assert r.confidence == "high"
        assert r.suggested_config == {"up_values": ["1"], "down_values": ["0"]}

    async def test_window_is_seven_days(self) -> None:
        a = self._adapter([0, 1])
        await pie_suitability.analyze_pie_suitability(a, 101)
        _, start, end = a.fetch_calls[0]
        assert end - start == 7 * 86400

    async def test_timeout_returns_unknown(self) -> None:
        """超过 5 秒 -> suitable=unknown，不抛错。"""
        a = self._adapter([0, 1], delay=pie_suitability.TIMEOUT_SECONDS + 0.5)
        r = await pie_suitability.analyze_pie_suitability(a, 101)
        assert r.suitable == "unknown"
        assert r.confidence == "unknown"
        assert "人工确认" in r.reason

    async def test_item_missing(self) -> None:
        a = FakeAdapter([], missing=True)
        r = await pie_suitability.analyze_pie_suitability(a, 999)
        assert r.suitable is False
        assert "不存在" in r.reason

    async def test_known_key_bumps_confidence(self) -> None:
        """key 含 icmpping 时置信度提升一级（medium -> high）。"""
        meta = {
            "item_id": 101,
            "name": "ICMP",
            "key": "icmpping",
            "value_type": 3,
            "units": "",
            "valuemap": {},
        }
        r = await pie_suitability.analyze_pie_suitability(self._adapter([0, 1, 2], meta=meta), 101)
        assert r.confidence == "high"
