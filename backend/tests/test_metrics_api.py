"""指标 API 集成测试（SQLite 内存库 + ASGITransport）。"""

from collections.abc import AsyncGenerator
from contextlib import contextmanager
from datetime import datetime, timezone

import httpx
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.deps import get_current_user
from app.database import get_db
from app.main import app
from app.models import Base
from app.models.user import User

ZABBIX_URL = "http://zabbix.example.com"
ENDPOINT = f"{ZABBIX_URL}/api_jsonrpc.php"

SEP = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())

DATASOURCE_PAYLOAD = {
    "name": "生产 Zabbix",
    "type": "zabbix",
    "config": {"url": ZABBIX_URL, "auth_type": "bearer", "timeout": 10, "verify_ssl": False},
    "secret": "zabbix-token-xyz7890",
}

METRIC_PAYLOAD = {
    "metric_type": "scalar",
    "name": "cpu_usage",
    "data_source_id": 1,
    "query_config": {
        "host_group_ids": ["4"],
        "host_ids": ["10554"],
        "item_ids": ["42253"],
    },
    "calc_formula": {"agg": "avg", "compare": "mom", "baseline_lookback": 1},
    "unit": "%",
    "tags": ["核心", "网络"],
}


def rpc(result: object, req_id: int) -> httpx.Response:
    return httpx.Response(
        200, json={"jsonrpc": "2.0", "result": result, "id": req_id}
    )


# 名称解析（详情接口 query_config_resolved）用到的三个 RPC
_HOSTGROUP = [{"groupid": "4", "name": "Linux servers"}]
_HOST = [
    {
        "hostid": "10554",
        "name": "web-01",
        "hostgroups": [{"groupid": "4", "name": "Linux servers"}],
    }
]
_ITEM = [
    {
        "itemid": "42253",
        "name": "CPU utilization",
        "key_": "system.cpu.util",
        "hostid": "10554",
        "value_type": "0",
        "units": "%",
        "hosts": [{"hostid": "10554", "name": "web-01"}],
    }
]


@contextmanager
def _mock_resolve():
    """让详情接口的名称解析不发起真实 HTTP。"""
    with respx.mock:
        respx.post(ENDPOINT).mock(
            side_effect=[rpc(_HOSTGROUP, 1), rpc(_HOST, 2), rpc(_ITEM, 3)]
        )
        yield


@pytest_asyncio.fixture
async def api(fake_user: User) -> AsyncGenerator[tuple[AsyncClient, async_sessionmaker[AsyncSession]], None]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: fake_user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, session_factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def _create_metric(client: AsyncClient) -> dict:
    resp = await client.post("/api/datasources", json=DATASOURCE_PAYLOAD)
    assert resp.status_code == 201, resp.text
    resp = await client.post("/api/metrics", json=METRIC_PAYLOAD)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestCRUD:
    async def test_create_and_read(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        body = await _create_metric(client)
        assert body["query_config"]["item_ids"] == ["42253"]
        assert body["tags"] == ["核心", "网络"]
        assert body["calc_formula"]["baseline_lookback"] == 1
        assert body["is_active"] is True
        with _mock_resolve():
            detail = await client.get(f"/api/metrics/{body['id']}")
        assert detail.status_code == 200
        # 详情额外返回 id -> name 解析结果（前端回填下拉用）
        resolved = detail.json()["query_config_resolved"]
        assert resolved["host_groups"] == [{"id": "4", "name": "Linux servers"}]
        assert resolved["hosts"] == [{"id": "10554", "name": "web-01"}]
        assert resolved["items"][0]["name"] == "CPU utilization"

    @respx.mock
    async def test_detail_resolved_null_when_datasource_down(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """数据源不可达时 query_config_resolved 为 null（前端降级显示 id）。"""
        client, _ = api
        body = await _create_metric(client)
        respx.post(ENDPOINT).mock(return_value=httpx.Response(500))
        detail = await client.get(f"/api/metrics/{body['id']}")
        assert detail.status_code == 200
        assert detail.json()["query_config_resolved"] is None

    async def test_tags_normalized(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """标签去空白 / 去重 / 保序。"""
        client, _ = api
        await client.post("/api/datasources", json=DATASOURCE_PAYLOAD)
        payload = {**METRIC_PAYLOAD, "tags": ["  核心  ", "核心", "", "网络"]}
        resp = await client.post("/api/metrics", json=payload)
        assert resp.status_code == 201
        assert resp.json()["tags"] == ["核心", "网络"]

    async def test_scope_requires_all_three_levels(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """三级缺一不可（防绕过前端直连 API 造成口径不可审计）。"""
        client, _ = api
        await client.post("/api/datasources", json=DATASOURCE_PAYLOAD)
        for qc in (
            {"host_ids": ["10554"], "item_ids": ["42253"]},
            {"host_group_ids": ["4"], "item_ids": ["42253"]},
            {"host_group_ids": ["4"], "host_ids": ["10554"]},
        ):
            resp = await client.post(
                "/api/metrics", json={**METRIC_PAYLOAD, "query_config": qc}
            )
            assert resp.status_code == 422, qc
            assert "取数范围" in resp.text or "监控项" in resp.text, resp.text

    async def test_empty_list_item_ids_rejected(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """item_ids 为 [] 视为未提供，同样拒绝。"""
        client, _ = api
        await client.post("/api/datasources", json=DATASOURCE_PAYLOAD)
        payload = {
            **METRIC_PAYLOAD,
            "query_config": {
                "host_group_ids": ["4"],
                "host_ids": ["10554"],
                "item_ids": [],
            },
        }
        resp = await client.post("/api/metrics", json=payload)
        assert resp.status_code == 422

    async def test_blank_ids_filtered_then_rejected(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """空白 id 被清洗后为空 -> 422。"""
        client, _ = api
        await client.post("/api/datasources", json=DATASOURCE_PAYLOAD)
        payload = {
            **METRIC_PAYLOAD,
            "query_config": {
                "host_group_ids": ["4"],
                "host_ids": ["  "],
                "item_ids": ["42253"],
            },
        }
        resp = await client.post("/api/metrics", json=payload)
        assert resp.status_code == 422

    async def test_invalid_agg_rejected(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        await client.post("/api/datasources", json=DATASOURCE_PAYLOAD)
        payload = {**METRIC_PAYLOAD, "calc_formula": {"agg": "median"}}
        resp = await client.post("/api/metrics", json=payload)
        assert resp.status_code == 422

    async def test_mom_without_lookback_rejected(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        await client.post("/api/datasources", json=DATASOURCE_PAYLOAD)
        payload = {**METRIC_PAYLOAD, "calc_formula": {"agg": "avg", "compare": "mom"}}
        resp = await client.post("/api/metrics", json=payload)
        assert resp.status_code == 422

    async def test_invalid_datasource_rejected(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        resp = await client.post("/api/metrics", json=METRIC_PAYLOAD)
        assert resp.status_code == 422

    async def test_duplicate_name_conflict(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        await _create_metric(client)
        resp = await client.post("/api/metrics", json=METRIC_PAYLOAD)
        assert resp.status_code == 409

    async def test_soft_delete_and_include_inactive(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        body = await _create_metric(client)
        resp = await client.delete(f"/api/metrics/{body['id']}")
        assert resp.status_code == 204
        assert (await client.get("/api/metrics")).json() == []
        with_inactive = await client.get("/api/metrics?include_inactive=true")
        assert len(with_inactive.json()) == 1
        assert with_inactive.json()[0]["is_active"] is False


    @respx.mock
    async def test_list_includes_resolved_names(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """列表直接带名称解析（前端展示名称而非 id）；同数据源只 3 次 RPC。"""
        client, _ = api
        await _create_metric(client)
        await client.post(
            "/api/metrics",
            json={**METRIC_PAYLOAD, "name": "cpu_usage_2", "tags": []},
        )
        route = respx.post(ENDPOINT).mock(
            side_effect=[rpc(_HOSTGROUP, 1), rpc(_HOST, 2), rpc(_ITEM, 3)]
        )
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        first = body[0]["query_config_resolved"]
        assert first["host_groups"][0]["name"] == "Linux servers"
        assert first["hosts"][0]["name"] == "web-01"
        assert first["items"][0]["name"] == "CPU utilization"
        # 2 条指标同数据源 -> 仍然只有 3 次 RPC（按数据源批量）
        assert len(route.calls) == 3

    @respx.mock
    async def test_list_resolved_null_when_datasource_down(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        await _create_metric(client)
        respx.post(ENDPOINT).mock(return_value=httpx.Response(500))
        body = (await client.get("/api/metrics")).json()
        assert body[0]["query_config_resolved"] is None


class TestCompute:
    @respx.mock
    async def test_compute_end_to_end(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        body = await _create_metric(client)

        def rpc(result: object, req_id: int) -> httpx.Response:
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "result": result, "id": req_id}
            )

        respx.post(ENDPOINT).mock(
            side_effect=[
                # host.get（resolve_items 的 host_ids 归属校验）
                rpc(_HOST, 1),
                # item.get
                rpc(_ITEM, 2),
                # trend.get 本期
                rpc(
                    [
                        {
                            "itemid": "42253",
                            "clock": str(SEP + 3600),
                            "num": "60",
                            "value_min": "15.0",
                            "value_avg": "20.0",
                            "value_max": "25.0",
                        }
                    ],
                    3,
                ),
                # trend.get 基线
                rpc(
                    [
                        {
                            "itemid": "42253",
                            "clock": "1754586000",
                            "num": "60",
                            "value_min": "8.0",
                            "value_avg": "10.0",
                            "value_max": "12.0",
                        }
                    ],
                    4,
                ),
                # hostgroup.get（object / formula 描述）
                rpc(_HOSTGROUP, 5),
            ]
        )
        resp = await client.post(
            f"/api/metrics/{body['id']}/compute", json={"period": "2026-09"}
        )
        assert resp.status_code == 200, resp.text
        fact = resp.json()
        assert fact["value"] == 20.0
        assert fact["baseline_value"] == 10.0
        assert fact["change_pct"] == 100.0
        assert fact["source"] == "生产 Zabbix"
        assert "Linux servers" in fact["object"]
        assert fact["period"] == "2026-09"

    async def test_compute_invalid_period_format(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        body = await _create_metric(client)
        resp = await client.post(
            f"/api/metrics/{body['id']}/compute", json={"period": "2026-9"}
        )
        assert resp.status_code == 422


class TestTagFilter:
    async def _seed(
        self, client: AsyncClient, name: str, tags: list[str]
    ) -> None:
        await client.post(
            "/api/metrics", json={**METRIC_PAYLOAD, "name": name, "tags": tags}
        )

    async def test_filter_by_single_tag(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        await _create_metric(client)  # tags = ["核心", "网络"]
        await self._seed(client, "mem", ["边缘"])
        resp = await client.get("/api/metrics?tag=核心")
        assert resp.status_code == 200
        assert [m["name"] for m in resp.json()] == ["cpu_usage"]

    async def test_filter_multiple_tags_is_and(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        await _create_metric(client)  # ["核心", "网络"]
        await self._seed(client, "only_core", ["核心"])
        resp = await client.get("/api/metrics?tag=核心&tag=网络")
        assert [m["name"] for m in resp.json()] == ["cpu_usage"]

    async def test_filter_no_match(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        await _create_metric(client)
        resp = await client.get("/api/metrics?tag=不存在的标签")
        assert resp.json() == []

    async def test_no_tag_returns_all(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        await _create_metric(client)
        await self._seed(client, "mem", [])
        assert len((await client.get("/api/metrics")).json()) == 2
