"""`_resolve_many` 并发化与降级语义的回归。

改前：数据源循环与分桶循环都是串行await，一次列表请求要把每套外部系统的
「清单 + 实例 + 监控项」依次等完（实测两套数据源串起来 2.28 秒）。
改后：数据源之间、同一数据源内各桶之间都并发。

要锁住两件事：
1. 真的并发了（而不是看起来并发）
2. 并发化没有破坏「单个数据源失败只降级它自己」的语义
"""

import asyncio

import pytest

from app.api import metrics as metrics_api
from app.api.metrics import _resolve_many

pytestmark = pytest.mark.asyncio


class _SlowAdapter:
    """每次外部调用都 sleep，用来观察是否真的并发。"""

    scope_kind = "host"

    def __init__(self, delay: float = 0.05, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail
        self.concurrent_peak = 0
        self._inflight = 0

    async def _tick(self) -> None:
        self._inflight += 1
        self.concurrent_peak = max(self.concurrent_peak, self._inflight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self._inflight -= 1
        if self.fail:
            raise RuntimeError("上游不可用")

    async def list_host_groups(self):
        await self._tick()
        return [{"group_id": 4, "name": "G4"}]

    async def list_hosts(self, group_ids, q=None):
        await self._tick()
        return [{"host_id": 10, "name": "web", "host_id_raw": group_ids[0]}]

    async def list_items(self, host_ids, value_types=None):
        await self._tick()
        return [{"item_id": 100, "name": "CPU", "host_id": 10, "host": "web"}]

    async def resolve_scoped_items(self, *, upper_ids, lower_ids, keys):
        await self._tick()
        return [
            {
                "item_id": 1,
                "name": f"item-{k}",
                "host_id": lower_ids[0] if lower_ids else "",
                "host": "web",
            }
            for k in keys
        ]


class _Metric:
    def __init__(self, mid: int, ds_id: int, qc: dict) -> None:
        self.id = mid
        self.data_source_id = ds_id
        self.query_config = qc
        self.query_config_resolved = None


def _metric(mid: int, ds_id: int, host: str = "10", n_items: int = 2) -> _Metric:
    return _Metric(
        mid,
        ds_id,
        {
            "host_group_ids": ["4"],
            "host_ids": [host],
            "item_ids": [str(200 + i) for i in range(n_items)],
        },
    )


def _patch(monkeypatch, adapters: dict[int, _SlowAdapter]) -> list[_SlowAdapter]:
    used: list[_SlowAdapter] = []

    async def fake_load(db, ds_id):
        a = adapters[ds_id]
        used.append(a)
        return a, f"ds{ds_id}"

    monkeypatch.setattr(metrics_api, "load_adapter_and_name", fake_load)
    return used


class TestConcurrency:
    async def test_two_datasources_run_in_parallel(self, monkeypatch) -> None:
        """两套数据源应并发：总耗时≈ 单次，而非两次之和。"""
        adapters = {1: _SlowAdapter(0.05), 2: _SlowAdapter(0.05)}
        _patch(monkeypatch, adapters)
        metrics = [_metric(1, 1), _metric(2, 2)]

        loop = asyncio.get_running_loop()
        t0 = loop.time()
        out = await _resolve_many(None, metrics)  # type: ignore[arg-type]
        elapsed = loop.time() - t0

        assert set(out) == {1, 2}
        assert elapsed < 0.09, f"两套数据源串行了（耗时 {elapsed:.3f}s，应≈0.05s）"
        for a in adapters.values():
            assert a.concurrent_peak >= 1

    async def test_buckets_in_one_datasource_run_in_parallel(
        self, monkeypatch
    ) -> None:
        """同一数据源内、主机集合不同的多个桶也应并发。"""
        a = _SlowAdapter(0.05)
        _patch(monkeypatch, {1: a})
        metrics = [_metric(1, 1, host="10"), _metric(2, 1, host="11")]

        loop = asyncio.get_running_loop()
        t0 = loop.time()
        out = await _resolve_many(None, metrics)  # type: ignore[arg-type]
        elapsed = loop.time() - t0

        assert set(out) == {1, 2}
        assert elapsed < 0.09, f"桶之间串行了（耗时 {elapsed:.3f}s）"
        # 清单 + 两个桶共3 个调用，其中至少两个同时在飞
        assert a.concurrent_peak >= 2, "并发峰值应至少为 2"

    async def test_concurrency_is_capped(self, monkeypatch) -> None:
        """并发数受_SEMAPHORE 限制，避免上游被打爆。"""
        a = _SlowAdapter(0.02)
        _patch(monkeypatch, {1: a})
        metrics = [_metric(i, 1, host=str(10 + i)) for i in range(1, 9)]

        await _resolve_many(None, metrics)  # type: ignore[arg-type]

        cap = metrics_api._RESOLVE_CONCURRENCY
        assert a.concurrent_peak <= cap, (
            f"并发峰值 {a.concurrent_peak} 超过了上限 {cap}"
        )


class TestDegradation:
    async def test_one_datasource_failure_does_not_affect_other(
        self, monkeypatch
    ) -> None:
        """数据源 2 挂了，数据源 1 的指标仍要解析出名称（用户已选定的降级语义）。"""
        _patch(monkeypatch, {1: _SlowAdapter(0.01), 2: _SlowAdapter(0.01, fail=True)})
        metrics = [_metric(1, 1), _metric(2, 2)]

        out = await _resolve_many(None, metrics)  # type: ignore[arg-type]

        assert 1 in out, "健康数据源的结果不该被失败的数据源带累"
        assert out[1].host_groups[0].name == "G4"
        assert 2 not in out, "失败的数据源应降级为不解析"

    async def test_all_failed_returns_empty_without_raising(
        self, monkeypatch
    ) -> None:
        """全部失败时返回空 dict，不抛异常 —— 列表页仍要能显示指标本身。"""
        _patch(
            monkeypatch,
            {1: _SlowAdapter(0.01, fail=True), 2: _SlowAdapter(0.01, fail=True)},
        )
        out = await _resolve_many(None, [_metric(1, 1), _metric(2, 2)])  # type: ignore[arg-type]
        assert out == {}

    async def test_missing_scope_is_skipped_quietly(self, monkeypatch) -> None:
        """query_config 缺范围（不该发生，但要有兜底）不应触发外部调用。"""
        a = _SlowAdapter(0.01)
        _patch(monkeypatch, {1: a})
        m = _Metric(9, 1, {"host_group_ids": [], "host_ids": []})
        out = await _resolve_many(None, [m])  # type: ignore[arg-type]
        assert out == {}
        assert a.concurrent_peak == 0, "缺范围时不该调外部API"

    async def test_time_metric_skipped(self, monkeypatch) -> None:
        """time 类型没有数据源，不该产生任何调用。"""
        a = _SlowAdapter(0.01)
        _patch(monkeypatch, {1: a})
        m = _Metric(10, None, {})
        assert await _resolve_many(None, [m]) == {}  # type: ignore[arg-type]
        assert a.concurrent_peak == 0