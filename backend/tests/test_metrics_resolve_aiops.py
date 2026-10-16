"""`_resolve_many` 对 AIOps 指标的名称解析回归。

修复前该函数硬编码读``host_group_ids`` / ``host_ids``，而 AIOps 指标存的是
``model_ids`` / ``instance_ids``，于是整段被 ``if not union_groups or not
union_hosts: continue`` 跳过 —— 列表页与编辑页回显退化成裸 id / 「未知主机组」。
这里锁住两条语义：Zabbix 路径逐字不变、AIOps 路径能解析出模型 / 实例 / 取数对象名。
"""

from typing import Any

import pytest

from app.api.metrics import _resolve_many
from app.services.adapters.base import DataSourceAdapter


class _FakeAdapter(DataSourceAdapter):
    """最小可用 adapter：只实现 _resolve_many 会用到的那几个方法。"""

    def __init__(self, *, aiops: bool, fail: bool = False) -> None:
        super().__init__({}, None)
        self._aiops = aiops
        self._fail = fail
        # 真实的 AIOpsAdapter 声明了 scope_kind = "model_instance"；
        # 测试替身必须一并声明，否则 _resolve_many 会按 Zabbix 语义读 host_* 字段
        self.scope_kind = "model_instance" if aiops else "host"
        self.resolve_calls: list[dict[str, Any]] = []

    async def test_connection(self) -> bool:
        return True

    async def list_host_groups(self) -> list[dict[str, Any]]:
        if self._aiops:
            return [
                {"group_id": 22, "name": "路由交换"},
                {"group_id": 99, "name": "未用模型"},
            ]
        return [{"group_id": 4, "name": "Linux servers"}]

    async def list_hosts(self, group_ids: list[str], q: str | None = None):
        if self._aiops:
            return [{"host_id": 14009, "name": "缅甸Mytel-10.100.100.251-ASW"}]
        return [{"host_id": 10554, "name": "host-1"}]

    async def list_items(self, host_ids, value_types=None):
        if self._aiops:
            return [{"item_id": 9669, "name": "端口表", "host_id": 14009}]
        return [{"item_id": 42253, "name": "CPU使用率", "host_id": 10554}]

    async def normalize_item_keys(self, keys: list[str]) -> dict[str, int]:
        if self._fail:
            raise RuntimeError("boom")
        return {k: i + 1 for i, k in enumerate(keys)}

    async def resolve_scoped_items(self, *, upper_ids, lower_ids, keys):
        if self._fail:
            raise RuntimeError("boom")
        self.resolve_calls.append(
            {"upper": list(upper_ids), "lower": list(lower_ids), "keys": list(keys)}
        )
        if self._aiops:
            from app.services.aiops_keys import decode_ref

            out = []
            for i, k in enumerate(keys):
                ref = decode_ref(k)
                out.append(
                    {
                        "item_id": i + 1,
                        "name": f"{ref.item_id} 的列",
                        "host_id": str(ref.instance_id),
                        "host": "缅甸Mytel-10.100.100.251-ASW",
                    }
                )
            return out
        return [
            {
                "item_id": i + 1,
                "name": f"item-{i + 1}",
                "host_id": "10554",
                "host": "host-1",
            }
            for i, _ in enumerate(keys)
        ]

    async def fetch_latest_value(self, item_id, time_from=None, time_till=None):
        return None

    async def get_item_meta(self, item_id):
        return None

    async def fetch_trend_data(self, item_ids, time_from, time_till):
        return []


@pytest.fixture
def patched(monkeypatch):
    """把 load_adapter_and_name 换成可控 fake。"""

    def _install(adapter: _FakeAdapter) -> _FakeAdapter:
        async def _fake(db, ds_id):  # noqa: ANN001
            return adapter, f"ds{ds_id}"

        monkeypatch.setattr("app.api.metrics.load_adapter_and_name", _fake)
        return adapter

    return _install


class _Metric:
    def __init__(self, mid: int, ds_id: int | None, query_config: dict) -> None:
        self.id = mid
        self.data_source_id = ds_id
        self.query_config = query_config
        self.query_config_resolved = None


class TestResolveMany:
    @pytest.mark.asyncio
    async def test_zabbix_path_unchanged(self, patched) -> None:
        adapter = patched(_FakeAdapter(aiops=False))
        m = _Metric(
            1,
            7,
            {"host_group_ids": ["4"], "host_ids": ["10554"], "item_ids": ["42253"]},
        )
        out = await _resolve_many(None, [m])  # type: ignore[arg-type]
        got = out[1]
        assert [g.name for g in got.host_groups] == ["Linux servers"]
        assert [h.name for h in got.hosts] == ["host-1"]
        assert [i.name for i in got.items] == ["item-1"]
        # Zabbix 必须走 host_* 字段，不能误读 model_ids
        assert adapter.resolve_calls[0]["upper"] == ["4"]

    @pytest.mark.asyncio
    async def test_aiops_path_resolves_names(self, patched) -> None:
        adapter = patched(_FakeAdapter(aiops=True))
        m = _Metric(
            2,
            9,
            {
                "model_ids": ["22"],
                "instance_ids": ["14009"],
                "item_ids": ["n:14009:10143", "l:14009:9669:11504:UG9ydA=="],
            },
        )
        out = await _resolve_many(None, [m])  # type: ignore[arg-type]
        got = out[2]
        assert [g.name for g in got.host_groups] == ["路由交换"]
        assert [h.name for h in got.hosts] == ["缅甸Mytel-10.100.100.251-ASW"]
        assert len(got.items) == 2
        # 定位串原样回传给前端，不能被改写
        assert [i.id for i in got.items] == [
            "n:14009:10143",
            "l:14009:9669:11504:UG9ydA==",
        ]
        assert adapter.resolve_calls[0]["upper"] == ["22"]

    @pytest.mark.asyncio
    async def test_curve_series_ids_are_resolved(self, patched) -> None:
        """curve 的取数对象在 series 里，不在 item_ids。"""
        patched(_FakeAdapter(aiops=True))
        m = _Metric(
            3,
            9,
            {
                "model_ids": ["22"],
                "instance_ids": ["14009"],
                "item_ids": [],
                "series": [{"item_id": "n:14009:10143"}, {"item_id": "n:14009:10141"}],
            },
        )
        got = (await _resolve_many(None, [m]))[3]  # type: ignore[arg-type]
        assert [i.id for i in got.items] == ["n:14009:10143", "n:14009:10141"]

    @pytest.mark.asyncio
    async def test_adapter_failure_degrades_to_absent(self, patched) -> None:
        """数据源不可达时不报错，只是这一批没有解析结果。"""
        patched(_FakeAdapter(aiops=True, fail=True))
        m = _Metric(
            4, 9, {"model_ids": ["22"], "instance_ids": ["14009"], "item_ids": ["n:1:2"]}
        )
        assert await _resolve_many(None, [m]) == {}  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_time_metric_skipped(self, patched) -> None:
        """time 类型没有数据源，不该产生任何解析调用。"""
        adapter = patched(_FakeAdapter(aiops=True))
        m = _Metric(5, None, {})
        assert await _resolve_many(None, [m]) == {}  # type: ignore[arg-type]
        assert adapter.resolve_calls == []

    @pytest.mark.asyncio
    async def test_rpc_count_independent_of_metric_count(self, patched) -> None:
        """**防性能回归**：同一批主机的 N 个指标只做一次 resolve_scoped_items。

        修复前resolve_scoped_items 被放在「每个指标一次」的循环里，
        N 个指标 = N×(list_hosts + list_items) 次 RPC，列表页从几十毫秒
        退化到数秒（实测 4.4kB 响应要 2.76 秒）。
        """
        adapter = patched(_FakeAdapter(aiops=True))
        metrics = [
            _Metric(
                100 + i,
                9,
                {
                    "model_ids": ["22"],
                    "instance_ids": ["14009"],
                    "item_ids": [f"n:14009:{200 + i}"],
                },
            )
            for i in range(25)
        ]
        out = await _resolve_many(None, metrics)  # type: ignore[arg-type]
        assert len(out) == 25
        # 25 个指标 -> 仍然只有一次调用
        assert len(adapter.resolve_calls) == 1
        # 且这一次就把 25 个取数对象都解析了
        assert len(adapter.resolve_calls[0]["keys"]) == 25

    @pytest.mark.asyncio
    async def test_distinct_host_sets_are_resolved_separately(self, patched) -> None:
        """下级集合不同才需要多次调用；相同就合并。"""
        adapter = patched(_FakeAdapter(aiops=True))
        metrics = [
            _Metric(
                200 + i,
                9,
                {
                    "model_ids": ["22"],
                    "instance_ids": [str(14009 + i)],
                    "item_ids": [f"n:{14009 + i}:1"],
                },
            )
            for i in range(3)
        ]
        await _resolve_many(None, metrics)  # type: ignore[arg-type]
        assert len(adapter.resolve_calls) == 3