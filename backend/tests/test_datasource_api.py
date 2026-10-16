"""数据源 API 集成测试（SQLite 内存库 + ASGITransport）。

注意：SQLite 下不验证部分唯一索引行为（仅 PG 生效），
名称冲突由 API 层校验覆盖。
"""

import json
from collections.abc import AsyncGenerator

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
from app.models.datasource import DataSource
from app.models.user import User
from app.services.crypto import decrypt

ZABBIX_URL = "http://zabbix.example.com"
ENDPOINT = f"{ZABBIX_URL}/api_jsonrpc.php"

CREATE_PAYLOAD = {
    "name": "生产 Zabbix",
    "type": "zabbix",
    "config": {
        "url": ZABBIX_URL,
        "auth_type": "bearer",
        "timeout": 10,
        "verify_ssl": False,
    },
    "secret": "zabbix-token-xyz7890",
}


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


async def _create(client: AsyncClient) -> dict:
    resp = await client.post("/api/datasources", json=CREATE_PAYLOAD)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestCreate:
    async def test_create_returns_masked_hint(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        body = await _create(client)
        assert body["secret_hint"] == "****7890"
        assert body["config"]["url"] == ZABBIX_URL
        # 响应中不得出现明文或密文
        assert "zabbix-token-xyz7890" not in json.dumps(body)
        assert "secret_encrypted" not in body

    async def test_secret_stored_encrypted(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, session_factory = api
        body = await _create(client)
        async with session_factory() as session:
            ds = await session.get(DataSource, body["id"])
            assert ds is not None
            assert ds.secret_encrypted is not None
            assert CREATE_PAYLOAD["secret"] not in ds.secret_encrypted
            assert decrypt(ds.secret_encrypted) == CREATE_PAYLOAD["secret"]
            assert ds.secret_hint == "****7890"

    async def test_duplicate_name_conflict(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        await _create(client)
        resp = await client.post("/api/datasources", json=CREATE_PAYLOAD)
        assert resp.status_code == 409


class TestRead:
    async def test_list_and_detail_masked(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        body = await _create(client)
        list_resp = await client.get("/api/datasources")
        assert list_resp.status_code == 200
        assert list_resp.json()[0]["secret_hint"] == "****7890"
        detail_resp = await client.get(f"/api/datasources/{body['id']}")
        assert detail_resp.json()["secret_hint"] == "****7890"
        assert CREATE_PAYLOAD["secret"] not in json.dumps(detail_resp.json())

    async def test_detail_404(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        assert (await client.get("/api/datasources/999")).status_code == 404


class TestUpdate:
    async def test_keep_secret_when_omitted_or_empty(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, session_factory = api
        body = await _create(client)
        async with session_factory() as session:
            original = (await session.get(DataSource, body["id"])).secret_encrypted  # type: ignore[union-attr]

        # 不传 secret
        resp = await client.put(
            f"/api/datasources/{body['id']}", json={"name": "生产 Zabbix 改名"}
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "生产 Zabbix 改名"
        # 传空字符串
        resp = await client.put(f"/api/datasources/{body['id']}", json={"secret": ""})
        assert resp.status_code == 200

        async with session_factory() as session:
            ds = await session.get(DataSource, body["id"])
            assert ds is not None
            assert ds.secret_encrypted == original
            assert decrypt(ds.secret_encrypted) == CREATE_PAYLOAD["secret"]  # type: ignore[arg-type]

    async def test_overwrite_secret(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, session_factory = api
        body = await _create(client)
        resp = await client.put(
            f"/api/datasources/{body['id']}", json={"secret": "new-token-0001abcd"}
        )
        assert resp.status_code == 200
        assert resp.json()["secret_hint"] == "****abcd"
        async with session_factory() as session:
            ds = await session.get(DataSource, body["id"])
            assert ds is not None
            assert decrypt(ds.secret_encrypted) == "new-token-0001abcd"  # type: ignore[arg-type]


class TestDelete:
    async def test_soft_delete(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, session_factory = api
        body = await _create(client)
        resp = await client.delete(f"/api/datasources/{body['id']}")
        assert resp.status_code == 204
        async with session_factory() as session:
            ds = await session.get(DataSource, body["id"])
            assert ds is not None
            assert ds.is_active is False

    async def test_deleted_disappears_from_list(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """软删除后必须从列表消失（回归：曾出现「提示删除成功、刷新后那行还在」）。

        界面上没有「恢复」入口，所以留着这行等于让用户删不掉它。
        """
        client, _ = api
        body = await _create(client)
        assert (await client.get("/api/datasources")).json()  # 先能看见
        assert (await client.delete(f"/api/datasources/{body['id']}")).status_code == 204

        listed = (await client.get("/api/datasources")).json()
        assert all(d["id"] != body["id"] for d in listed)

        # 但记录仍在库里（软删除，可审计）
        assert (await client.get(f"/api/datasources/{body['id']}")).status_code == 200
        # 需要排查时能显式要回来
        with_inactive = (await client.get("/api/datasources?include_inactive=true")).json()
        assert any(d["id"] == body["id"] for d in with_inactive)


class TestConnectionEndpoint:
    @respx.mock
    async def test_success(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        body = await _create(client)
        respx.post(ENDPOINT).mock(
            side_effect=[
                httpx.Response(200, json={"jsonrpc": "2.0", "result": "7.2.0", "id": 1}),
                httpx.Response(200, json={"jsonrpc": "2.0", "result": [], "id": 2}),
            ]
        )
        resp = await client.post(f"/api/datasources/{body['id']}/test")
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "message": "连接成功"}

    @respx.mock
    async def test_invalid_token_masked_message(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        body = await _create(client)
        respx.post(ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32602,
                        "message": "Invalid params.",
                        "data": "Not authorized",
                    },
                    "id": 1,
                },
            )
        )
        resp = await client.post(f"/api/datasources/{body['id']}/test")
        result = resp.json()
        assert result["success"] is False
        assert "认证失败" in result["message"]
        assert CREATE_PAYLOAD["secret"] not in result["message"]


def _rpc(result: object, req_id: int) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "result": result, "id": req_id})


class TestScopeEndpoints:
    """三级下拉端点：host-groups / hosts / items。"""

    @respx.mock
    async def test_host_groups(self, api) -> None:
        client, _ = api
        ds = await _create(client)
        respx.post(ENDPOINT).mock(
            return_value=_rpc(
                [{"groupid": "4", "name": "Linux servers"}], 1
            )
        )
        resp = await client.get(f"/api/datasources/{ds['id']}/host-groups")
        assert resp.status_code == 200
        # id 一律以字符串返回，与 query_config 口径一致
        assert resp.json() == [{"group_id": "4", "name": "Linux servers"}]

    async def test_hosts_requires_group_ids(self, api) -> None:
        client, _ = api
        ds = await _create(client)
        resp = await client.get(f"/api/datasources/{ds['id']}/hosts")
        assert resp.status_code == 400
        assert "group_ids" in resp.text

    @respx.mock
    async def test_hosts_params_and_shape(self, api) -> None:
        client, _ = api
        ds = await _create(client)
        route = respx.post(ENDPOINT).mock(
            return_value=_rpc(
                [
                    {
                        "hostid": "10554",
                        "name": "web-01",
                        "hostgroups": [{"groupid": "4", "name": "Linux servers"}],
                    }
                ],
                1,
            )
        )
        resp = await client.get(
            f"/api/datasources/{ds['id']}/hosts?group_ids=4,7"
        )
        assert resp.status_code == 200
        sent = json.loads(route.calls.last.request.content)
        assert sent["method"] == "host.get"
        assert sent["params"]["groupids"] == ["4", "7"]
        assert sent["params"]["selectHostGroups"] == ["groupid", "name"]
        assert resp.json() == [
            {
                "host_id": "10554",
                "name": "web-01",
                "groups": [{"group_id": "4", "name": "Linux servers"}],
            }
        ]

    async def test_items_requires_host_ids(self, api) -> None:
        client, _ = api
        ds = await _create(client)
        resp = await client.get(f"/api/datasources/{ds['id']}/items")
        assert resp.status_code == 400
        assert "host_ids" in resp.text

    @respx.mock
    async def test_items_params_and_shape(self, api) -> None:
        client, _ = api
        ds = await _create(client)
        route = respx.post(ENDPOINT).mock(
            return_value=_rpc(
                [
                    {
                        "itemid": "42253",
                        "name": "CPU utilization",
                        "key_": "system.cpu.util",
                        "hostid": "10554",
                        "value_type": "0",
                        "units": "%",
                        "hosts": [{"hostid": "10554", "name": "web-01"}],
                    }
                ],
                1,
            )
        )
        resp = await client.get(
            f"/api/datasources/{ds['id']}/items?host_ids=10554"
        )
        assert resp.status_code == 200
        sent = json.loads(route.calls.last.request.content)
        assert sent["method"] == "item.get"
        assert sent["params"]["hostids"] == ["10554"]
        assert resp.json() == [
            {
                "item_id": "42253",
                "name": "CPU utilization",
                "key": "system.cpu.util",
                "host_id": "10554",
                "host": "web-01",
                "value_type": 0,
                "units": "%",
            }
        ]

    @respx.mock
    async def test_items_q_filter(self, api) -> None:
        client, _ = api
        ds = await _create(client)
        respx.post(ENDPOINT).mock(
            return_value=_rpc(
                [
                    {
                        "itemid": "1",
                        "name": "CPU utilization",
                        "key_": "system.cpu.util",
                        "hostid": "10554",
                        "value_type": "0",
                        "units": "%",
                    },
                    {
                        "itemid": "2",
                        "name": "Free memory",
                        "key_": "vm.memory.size[free]",
                        "hostid": "10554",
                        "value_type": "0",
                        "units": "%",
                    },
                ],
                1,
            )
        )
        # name 子串
        resp = await client.get(
            f"/api/datasources/{ds['id']}/items?host_ids=10554&q=cpu"
        )
        assert [i["item_id"] for i in resp.json()] == ["1"]
        # key 子串
        resp = await client.get(
            f"/api/datasources/{ds['id']}/items?host_ids=10554&q=vm.memory"
        )
        assert [i["item_id"] for i in resp.json()] == ["2"]
        # 无匹配
        resp = await client.get(
            f"/api/datasources/{ds['id']}/items?host_ids=10554&q=zzz"
        )
        assert resp.json() == []

    async def test_limits_declared(self) -> None:
        from app.api.datasources import MAX_HOSTS_LIMIT, MAX_ITEMS_LIMIT

        assert MAX_HOSTS_LIMIT == 500
        assert MAX_ITEMS_LIMIT == 2000
