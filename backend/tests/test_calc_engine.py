"""calc_engine 单元测试（FakeAdapter 桩，不走 HTTP）。

时间戳常量独立计算（不复用 period_utils），避免循环验证。
"""

from datetime import datetime, timezone
from typing import Any

import pytest

from app.models.metric import MetricDefinition
from app.services.adapters.base import AdapterConnectionError, DataSourceAdapter
from app.services.calc_engine import (
    pie_labels,
    MetricComputeError,
    _two_stage_aggregate,
    compute_metric,
)

SEP = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())
OCT = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())
AUG = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())
PERIOD = "2026-09"


def row(
    item_id: int,
    clock: int,
    avg: float,
    mn: float | None = None,
    mx: float | None = None,
) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "clock": clock,
        "num": 60,
        "value_min": mn if mn is not None else avg,
        "value_avg": avg,
        "value_max": mx if mx is not None else avg,
    }


def make_item(item_id: int, host_id: int = 1, key: str = "system.cpu.util") -> dict[str, Any]:
    return {
        "item_id": item_id,
        "name": f"item-{item_id}",
        "key": key,
        "host_id": host_id,
        "host": f"host-{host_id}",
        "value_type": 0,
        "units": "%",
    }


def make_metric(
    query_config: dict[str, Any] | None = None,
    calc_formula: dict[str, Any] | None = None,
) -> MetricDefinition:
    return MetricDefinition(
        id=1,
        name="cpu_usage",
        data_source_id=1,
        # 三级全必填：默认对应 FakeAdapter 的 host-1 / item-101
        query_config=query_config
        or {"host_group_ids": ["4"], "host_ids": ["1"], "item_ids": ["101"]},
        calc_formula=calc_formula or {"agg": "avg", "compare": "none"},
        unit="%",
        period_type="monthly",
    )


class FakeAdapter(DataSourceAdapter):
    def __init__(
        self,
        items: list[dict[str, Any]],
        trend_map: dict[tuple[int, int], list[dict[str, Any]]],
        groups: list[dict[str, Any]] | None = None,
        groups_error: bool = False,
        hosts: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__({}, None)
        self._items = items
        self._trend_map = trend_map
        self._groups = groups if groups is not None else [{"group_id": 4, "name": "Linux servers"}]
        self._groups_error = groups_error
        self._hosts = (
            hosts
            if hosts is not None
            else [
                {
                    "host_id": 1,
                    "name": "host-1",
                    "groups": [{"group_id": 4, "name": "Linux servers"}],
                },
                {
                    "host_id": 2,
                    "name": "host-2",
                    "groups": [{"group_id": 4, "name": "Linux servers"}],
                },
            ]
        )
        self.fetch_calls: list[tuple[int, int]] = []

    async def test_connection(self) -> bool:
        return True

    async def list_host_groups(self) -> list[dict[str, Any]]:
        if self._groups_error:
            raise AdapterConnectionError("mock failure")
        return self._groups

    async def list_hosts(self, group_ids: list[str]) -> list[dict[str, Any]]:
        wanted = {int(g) for g in group_ids}
        return [
            h
            for h in self._hosts
            if any(g["group_id"] in wanted for g in h.get("groups", []))
        ]

    async def list_items(
        self, host_ids: list[str], value_types: list[int] | None = None
    ) -> list[dict[str, Any]]:
        return self._items

    async def fetch_latest_value(self, item_id: int):  # type: ignore[no-untyped-def]
        return None


    async def get_item_meta(self, item_id: int):
        return None
    async def fetch_trend_data(
        self, item_ids: list[int], time_from: int, time_till: int
    ) -> list[dict[str, Any]]:
        self.fetch_calls.append((time_from, time_till))
        return self._trend_map.get((time_from, time_till), [])


class TestTwoStageAggregate:
    """四种 agg 的二次聚合正确性（手算预期值）。"""

    def trend(self) -> list[dict[str, Any]]:
        h1, h2, h3 = SEP + 3600, SEP + 7200, SEP + 10800
        return [
            row(101, h1, avg=10, mn=5, mx=15),
            row(101, h2, avg=20, mn=15, mx=25),
            row(101, h3, avg=30, mn=25, mx=35),
            row(102, h1, avg=40, mn=35, mx=45),
            row(102, h2, avg=50, mn=45, mx=55),
            row(102, h3, avg=60, mn=55, mx=65),
        ]

    def test_avg(self) -> None:
        # per item avg: 20, 50 -> 跨 item avg: 35
        value, n = _two_stage_aggregate(self.trend(), "avg", "本期")
        assert value == pytest.approx(35.0) and n == 2

    def test_max_uses_value_max_column(self) -> None:
        # per item max(value_max): 35, 65 -> 65
        value, _ = _two_stage_aggregate(self.trend(), "max", "本期")
        assert value == pytest.approx(65.0)

    def test_min_uses_value_min_column(self) -> None:
        # per item min(value_min): 5, 35 -> 5
        value, _ = _two_stage_aggregate(self.trend(), "min", "本期")
        assert value == pytest.approx(5.0)

    def test_sum(self) -> None:
        # per item sum(value_avg): 60, 150 -> 210
        value, _ = _two_stage_aggregate(self.trend(), "sum", "本期")
        assert value == pytest.approx(210.0)

    def test_two_stage_not_naive(self) -> None:
        # item 点数不等时，两阶段 != 全部点混合平均
        trend = [
            row(101, SEP + 3600, avg=100),  # item101 只有 1 小时
            row(102, SEP + 3600, avg=10),
            row(102, SEP + 7200, avg=20),
            row(102, SEP + 10800, avg=30),
        ]
        # 两阶段: (100 + 20) / 2 = 60；混合平均: (100+10+20+30)/4 = 40
        value, _ = _two_stage_aggregate(trend, "avg", "本期")
        assert value == pytest.approx(60.0)

    def test_empty_raises(self) -> None:
        with pytest.raises(MetricComputeError, match="本期"):
            _two_stage_aggregate([], "avg", "本期")


class TestComputeMetricBasic:
    async def test_compare_none(self) -> None:
        items = [make_item(101, host_id=1), make_item(102, host_id=2)]
        trend = [row(101, SEP + 3600, 20), row(102, SEP + 3600, 40)]
        adapter = FakeAdapter(items, {(SEP, OCT): trend})
        fact = await compute_metric(
            make_metric(
                query_config={
                    "host_group_ids": ["4"],
                    "host_ids": ["1", "2"],
                    "item_ids": ["101", "102"],
                }
            ),
            PERIOD,
            adapter,
            source_name="生产 Zabbix",
        )
        assert fact.value == pytest.approx(30.0)
        assert fact.baseline_value is None and fact.change_pct is None
        assert len(adapter.fetch_calls) == 1  # compare=none 不发起基线取数
        assert fact.source == "生产 Zabbix"
        assert fact.period == PERIOD
        assert "Linux servers" in fact.object
        assert "2 台主机" in fact.object and "2 个监控项" in fact.object

    async def test_mom_baseline_range_and_change_pct(self) -> None:
        items = [make_item(101)]
        adapter = FakeAdapter(
            items,
            {
                (SEP, OCT): [row(101, SEP + 3600, 210)],
                (AUG, SEP): [row(101, AUG + 3600, 200)],
            },
        )
        fact = await compute_metric(
            make_metric(calc_formula={"agg": "avg", "compare": "mom", "baseline_lookback": 1}),
            PERIOD,
            adapter,
            "ds",
        )
        # 基线取数区间精确等于日历月平移（2026-08 整月）
        assert adapter.fetch_calls == [(SEP, OCT), (AUG, SEP)]
        assert fact.baseline_value == pytest.approx(200.0)
        assert fact.change_pct == pytest.approx(5.0)

    async def test_baseline_uses_same_column(self) -> None:
        """同口径断言：agg=max 时基线若误用 value_avg 列会得到 35 而非 40。"""
        items = [make_item(101)]
        adapter = FakeAdapter(
            items,
            {
                (SEP, OCT): [
                    row(101, SEP + 3600, avg=50, mx=10),
                    row(101, SEP + 7200, avg=50, mx=90),
                ],
                (AUG, SEP): [
                    row(101, AUG + 3600, avg=35, mx=30),
                    row(101, AUG + 7200, avg=35, mx=40),
                ],
            },
        )
        fact = await compute_metric(
            make_metric(calc_formula={"agg": "max", "compare": "mom", "baseline_lookback": 1}),
            PERIOD,
            adapter,
            "ds",
        )
        assert fact.value == pytest.approx(90.0)
        assert fact.baseline_value == pytest.approx(40.0)  # 不是 35

    async def test_baseline_zero_change_pct_none(self) -> None:
        items = [make_item(101)]
        adapter = FakeAdapter(
            items,
            {
                (SEP, OCT): [row(101, SEP + 3600, 50)],
                (AUG, SEP): [row(101, AUG + 3600, 0), row(101, AUG + 7200, 0)],
            },
        )
        fact = await compute_metric(
            make_metric(calc_formula={"agg": "avg", "compare": "mom", "baseline_lookback": 1}),
            PERIOD,
            adapter,
            "ds",
        )
        assert fact.baseline_value == pytest.approx(0.0)
        assert fact.change_pct is None

    async def test_baseline_missing_not_blocking(self) -> None:
        items = [make_item(101)]
        adapter = FakeAdapter(items, {(SEP, OCT): [row(101, SEP + 3600, 50)]})
        fact = await compute_metric(
            make_metric(calc_formula={"agg": "avg", "compare": "mom", "baseline_lookback": 1}),
            PERIOD,
            adapter,
            "ds",
        )
        assert fact.value == pytest.approx(50.0)
        assert fact.baseline_value is None and fact.change_pct is None

    async def test_empty_current_trend_raises(self) -> None:
        adapter = FakeAdapter([make_item(101)], {})
        with pytest.raises(MetricComputeError, match="本期"):
            await compute_metric(make_metric(), PERIOD, adapter, "ds")

    async def test_groups_failure_degrades_object(self) -> None:
        items = [make_item(101)]
        adapter = FakeAdapter(
            items, {(SEP, OCT): [row(101, SEP + 3600, 50)]}, groups_error=True
        )
        fact = await compute_metric(make_metric(), PERIOD, adapter, "ds")
        assert "group #4 (名称获取失败)" in fact.object

    async def test_formula_scope_shows_group_and_host(self) -> None:
        """formula 必须标注主机组与主机名称（审核人可见取数口径）。"""
        items = [make_item(101)]
        adapter = FakeAdapter(items, {(SEP, OCT): [row(101, SEP + 3600, 50)]})
        fact = await compute_metric(make_metric(), PERIOD, adapter, "ds")
        assert "avg(trend.hourly.value_avg)" in fact.formula
        assert "组: Linux servers" in fact.formula
        assert "主机: host-1" in fact.formula
        assert "avg over 1 items" in fact.formula


class TestResolveItems:
    async def test_item_ids_missing_raises(self) -> None:
        adapter = FakeAdapter([make_item(101)], {(SEP, OCT): [row(101, SEP + 3600, 1)]})
        metric = make_metric(
            query_config={
                "host_group_ids": ["4"],
                "host_ids": ["1"],
                "item_ids": ["101", "999"],
            }
        )
        with pytest.raises(MetricComputeError, match="999"):
            await compute_metric(metric, PERIOD, adapter, "ds")

    async def test_host_not_in_group_raises(self) -> None:
        """防御性：host_ids 不属于所选主机组 -> 报错（防绕过前端）。"""
        adapter = FakeAdapter([make_item(101)], {(SEP, OCT): [row(101, SEP + 3600, 1)]})
        metric = make_metric(
            query_config={
                "host_group_ids": ["4"],
                "host_ids": ["77"],
                "item_ids": ["101"],
            }
        )
        with pytest.raises(MetricComputeError, match="不属于所选主机组"):
            await compute_metric(metric, PERIOD, adapter, "ds")

    @pytest.mark.parametrize(
        "query_config",
        [
            {"host_ids": ["1"], "item_ids": ["101"]},  # 缺 host_group_ids
            {"host_group_ids": ["4"], "item_ids": ["101"]},  # 缺 host_ids
            {"host_group_ids": ["4"], "host_ids": ["1"]},  # 缺 item_ids
            {"host_group_ids": [], "host_ids": ["1"], "item_ids": ["101"]},
        ],
    )
    async def test_missing_scope_level_raises(self, query_config) -> None:
        adapter = FakeAdapter([make_item(101)], {(SEP, OCT): [row(101, SEP + 3600, 1)]})
        with pytest.raises(MetricComputeError, match="不能为空|至少需要一个监控项"):
            await compute_metric(
                make_metric(query_config=query_config), PERIOD, adapter, "ds"
            )

    async def test_non_numeric_id_raises(self) -> None:
        adapter = FakeAdapter([make_item(101)], {})
        metric = make_metric(
            query_config={
                "host_group_ids": ["4"],
                "host_ids": ["1"],
                "item_ids": ["abc"],
            }
        )
        with pytest.raises(MetricComputeError, match="非数字"):
            await compute_metric(metric, PERIOD, adapter, "ds")

    async def test_invalid_agg_raises(self) -> None:
        adapter = FakeAdapter([make_item(101)], {})
        metric = make_metric(calc_formula={"agg": "median"})
        with pytest.raises(MetricComputeError, match="agg"):
            await compute_metric(metric, PERIOD, adapter, "ds")

    async def test_invalid_period_raises(self) -> None:
        adapter = FakeAdapter([make_item(101)], {})
        with pytest.raises(MetricComputeError):
            await compute_metric(make_metric(), "2026-13", adapter, "ds")

    async def test_mom_without_lookback_raises(self) -> None:
        adapter = FakeAdapter([make_item(101)], {})
        metric = make_metric(calc_formula={"agg": "avg", "compare": "mom"})
        with pytest.raises(MetricComputeError, match="baseline_lookback"):
            await compute_metric(metric, PERIOD, adapter, "ds")


class TestPieLabels:
    """饼图标签语义推断（1/0 型 -> 通/断，其它 -> UP/DOWN）。"""

    @staticmethod
    def _m(pie_config: dict) -> MetricDefinition:
        return MetricDefinition(
            id=1,
            name="m",
            data_source_id=1,
            unit="",
            period_type="monthly",
            display_precision=None,
            metric_type="pie",
            query_config={
                "host_group_ids": ["4"],
                "host_ids": ["1"],
                "item_ids": ["101"],
            },
            type_config={"pie_config": pie_config},
            is_active=True,
        )

    def test_icmping_pattern(self) -> None:
        m = self._m({"up_values": [1], "down_values": [0], "unknown_mode": "exclude"})
        assert pie_labels(m) == ("通", "断")

    def test_inverted_pattern_is_not_translated(self) -> None:
        """0=通 / 1=断 属非标准组合，回退英文避免写出反向标签。"""
        m = self._m({"up_values": [0], "down_values": [1]})
        assert pie_labels(m) == ("UP", "DOWN")

    def test_multi_value_fallback(self) -> None:
        m = self._m({"up_values": [2, 3], "down_values": [0]})
        assert pie_labels(m) == ("UP", "DOWN")

    def test_missing_config_fallback(self) -> None:
        m = self._m({})
        assert pie_labels(m) == ("UP", "DOWN")
