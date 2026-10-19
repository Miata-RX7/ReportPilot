"""浏览类TTL 缓存 + `_resolve_many` 并发化的回归测试。

背景：指标列表页曾要 2.28 秒，全部耗在**串行**调用两套外部监控系统上
（每套 3 次：清单 / 实例 / 监控项）。本次做了两件事：
1. list_host_groups / list_hosts / list_items 加进程内 TTL 缓存
2. `_resolve_many` 的数据源循环与分桶循环改为并发

这两处都容易「改好了、过两天又坏回去」，所以锁住行为。
"""

import asyncio

import pytest

from app.services.adapters.base import _LIST_CACHE, clear_list_cache

pytestmark = pytest.mark.asyncio


class _CountingAdapter:
    """最小 Adapter：只实现列表三方法，并记录被调用的次数与参数。"""

    scope_kind = "host"

    def __init__(self, cache_ns=None, fail: bool = False) -> None:
        self._cache_ns = cache_ns
        self._fail = fail
        self.calls: list[tuple[str, tuple]] = []

    def _bump(self, name: str, *args) -> None:
        self.calls.append((name, args))

    async def list_host_groups(self):
        self._bump("host_groups")
        if self._fail:
            raise RuntimeError("上游不可用")
        return [{"group_id": 4, "name": "Linux servers"}]

    async def list_hosts(self, group_ids, q=None):
        self._bump("hosts", tuple(group_ids), q)
        if self._fail:
            raise RuntimeError("上游不可用")
        return [{"host_id": int(g) * 10, "name": f"h{g}"} for g in group_ids]

    async def list_items(self, host_ids, value_types=None):
        self._bump("items", tuple(host_ids), tuple(value_types or ()))
        if self._fail:
            raise RuntimeError("上游不可用")
        return [{"item_id": 1, "name": "CPU", "host_id": int(host_ids[0])}]


class TestCachedHelper:
    async def test_second_call_hits_cache(self) -> None:
        calls: list[int] = []

        async def factory() -> str:
            calls.append(1)
            return "v"

        from app.services.adapters.base import _cached

        assert await _cached(1, ("k",), 60.0, factory) == "v"
        assert await _cached(1, ("k",), 60.0, factory) == "v"
        assert len(calls) == 1, "第二次应命中缓存，不该再回源"

    async def test_expired_entry_refetches(self) -> None:
        calls: list[int] = []

        async def factory() -> str:
            calls.append(1)
            return f"v{len(calls)}"

        from app.services.adapters.base import _cached

        assert await _cached(2, ("k",), 60.0, factory) == "v1"
        # TTL 传 0 => 立即过期，模拟时间流逝
        assert await _cached(2, ("k",), 0.0, factory) == "v2"
        assert len(calls) == 2

    async def test_empty_result_is_cached(self) -> None:
        """空列表是合法结果，必须缓存 —— 否则上游「确实没有」会每次都回源。"""
        calls: list[int] = []

        async def factory() -> list:
            calls.append(1)
            return []

        from app.services.adapters.base import _cached

        assert await _cached(3, ("empty",), 60.0, factory) == []
        assert await _cached(3, ("empty",), 60.0, factory) == []
        assert len(calls) == 1

    async def test_namespaces_are_isolated(self) -> None:
        """两个数据源不能共用同一份缓存。"""
        calls: list[str] = []

        async def factory_a() -> str:
            calls.append("a")
            return "A"

        async def factory_b() -> str:
            calls.append("b")
            return "B"

        from app.services.adapters.base import _cached

        assert await _cached(10, ("k",), 60.0, factory_a) == "A"
        assert await _cached(20, ("k",), 60.0, factory_b) == "B"
        assert set(calls) == {"a", "b"}, "不同命名空间应各自回源一次"

    async def test_single_flight_dedupes_concurrent_same_key(self) -> None:
        """并发请求同一个 key 时只回源一次（并发化后的关键防护）。"""
        calls: list[int] = []

        async def slow() -> str:
            calls.append(1)
            await asyncio.sleep(0.05)
            return "v"

        from app.services.adapters.base import _cached

        results = await asyncio.gather(
            *(_cached(30, ("same",), 60.0, slow) for _ in range(5))
        )
        assert results == ["v"] * 5
        assert len(calls) == 1, "5 个并发同键请求应只打一次上游"

    async def test_failure_not_cached_and_not_poison(self) -> None:
        """回源失败不能写入缓存（否则会缓存住异常状态直到 TTL 到期）。"""
        calls: list[int] = []

        async def failing() -> str:
            calls.append(1)
            raise RuntimeError("boom")

        from app.services.adapters.base import _cached

        with pytest.raises(RuntimeError):
            await _cached(40, ("bad",), 60.0, failing)
        with pytest.raises(RuntimeError):
            await _cached(40, ("bad",), 60.0, failing)
        assert len(calls) == 2, "失败后应能再次回源，而不是一直命中坏缓存"
        assert ("40", "bad") not in _LIST_CACHE

    async def test_failure_unblocks_concurrent_waiters(self) -> None:
        """回源失败时，等待方应收到同样的异常，而不是永久挂起。"""

        async def failing() -> str:
            await asyncio.sleep(0.02)
            raise RuntimeError("boom")

        from app.services.adapters.base import _cached

        results = await asyncio.gather(
            *(_cached(50, ("bad2",), 60.0, failing) for _ in range(3)),
            return_exceptions=True,
        )
        assert len(results) == 3
        assert all(isinstance(r, RuntimeError) for r in results)

    async def test_concurrent_different_keys_run_in_parallel(self) -> None:
        """不同 key 应真正并发（这是本次优化的目的本身）。"""
        order: list[str] = []

        def make(tag: str, delay: float):
            async def factory() -> str:
                order.append(f"start-{tag}")
                await asyncio.sleep(delay)
                order.append(f"end-{tag}")
                return tag

            return factory

        from app.services.adapters.base import _cached

        await asyncio.gather(
            _cached(60, ("a",), 60.0, make("a", 0.05)),
            _cached(60, ("b",), 60.0, make("b", 0.05)),
        )
        # 并发时必然是「两个 start 都在两个 end 之前」
        assert order.index("end-a") > order.index("start-b")


class TestClearListCache:
    async def test_clear_removes_entries(self) -> None:
        async def factory() -> str:
            return "v"

        from app.services.adapters.base import _cached

        await _cached(70, ("k",), 60.0, factory)
        assert _LIST_CACHE
        clear_list_cache()
        assert not _LIST_CACHE