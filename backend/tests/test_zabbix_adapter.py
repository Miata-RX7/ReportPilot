"""ZabbixAdapter 单元测试（respx mock HTTP）。

安全测试：token 明文不得出现在任何日志输出中。
"""

import json
import logging
from typing import Any

import httpx
import pytest
import respx

from app.services.adapters.base import (
    AdapterAPIError,
    AdapterAuthError,
    AdapterConnectionError,
    AdapterPermissionError,
)
from app.services.adapters.zabbix_adapter import ZabbixAdapter

ZABBIX_URL = "http://zabbix.example.com"
ENDPOINT = f"{ZABBIX_URL}/api_jsonrpc.php"
TOKEN = "test-token-abcdef987654"


def make_adapter(**config_overrides: Any) -> ZabbixAdapter:
    config: dict[str, Any] = {
        "url": ZABBIX_URL,
        "timeout": 5,
        "verify_ssl": False,
        "max_retries": 2,
    }
    config.update(config_overrides)
    return ZabbixAdapter(config, TOKEN)


def rpc_ok(result: Any, req_id: int = 1) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "result": result, "id": req_id})


def rpc_err(code: int, message: str, data: str = "") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "error": {"code": code, "message": message, "data": data},
            "id": 1,
        },
    )


class TestEndpoint:
    def test_api_path_auto_appended(self) -> None:
        assert make_adapter()._endpoint == ENDPOINT

    def test_full_path_not_duplicated(self) -> None:
        adapter = make_adapter(url=ENDPOINT)
        assert adapter._endpoint == ENDPOINT


class TestConnection:
    @respx.mock
    async def test_success(self) -> None:
        route = respx.post(ENDPOINT).mock(
            side_effect=[
                rpc_ok("7.2.0"),
                rpc_ok([{"groupid": "4"}], req_id=2),
            ]
        )
        assert await make_adapter().test_connection() is True
        assert route.call_count == 2
        # 首个探测调用（apiinfo.version）不得携带认证头：
        # Zabbix 7.x 会对其返回 -32602 "must be called without authorization header"
        assert "Authorization" not in route.calls[0].request.headers
        # 认证调用正常携带（token 只应出现在请求头而非日志）
        assert (
            route.calls[1].request.headers["Authorization"] == f"Bearer {TOKEN}"
        )

    @respx.mock
    async def test_invalid_token(self) -> None:
        respx.post(ENDPOINT).mock(
            return_value=rpc_err(-32602, "Invalid params.", "Not authorized: token")
        )
        with pytest.raises(AdapterAuthError):
            await make_adapter().test_connection()

    @respx.mock
    async def test_permission_denied(self) -> None:
        respx.post(ENDPOINT).mock(
            side_effect=[
                rpc_ok("7.2.0"),
                rpc_err(-32602, "Invalid params.", "No permissions to call"),
            ]
        )
        with pytest.raises(AdapterPermissionError):
            await make_adapter().test_connection()

    @respx.mock
    async def test_unreachable_after_retries(self) -> None:
        route = respx.post(ENDPOINT).mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        adapter = make_adapter(max_retries=2)
        with pytest.raises(AdapterConnectionError):
            await adapter.test_connection()
        assert route.call_count == 3  # 1 + 2 次重试

    @respx.mock
    async def test_server_error_retried(self) -> None:
        route = respx.post(ENDPOINT).mock(
            side_effect=[
                httpx.Response(502),
                rpc_ok("7.2.0"),
                rpc_ok([], req_id=2),
            ]
        )
        assert await make_adapter().test_connection() is True
        assert route.call_count == 3

    async def test_missing_token(self) -> None:
        adapter = ZabbixAdapter({"url": ZABBIX_URL}, secret=None)
        with pytest.raises(AdapterAuthError, match="未配置"):
            await adapter.test_connection()


class TestListHostGroups:
    @respx.mock
    async def test_parse(self) -> None:
        respx.post(ENDPOINT).mock(
            return_value=rpc_ok(
                [
                    {"groupid": "4", "name": "Linux servers"},
                    {"groupid": "6", "name": "Network devices"},
                ]
            )
        )
        groups = await make_adapter().list_host_groups()
        assert groups == [
            {"group_id": 4, "name": "Linux servers"},
            {"group_id": 6, "name": "Network devices"},
        ]


class TestListItems:
    @respx.mock
    async def test_params_and_parse(self) -> None:
        route = respx.post(ENDPOINT).mock(
            return_value=rpc_ok(
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
                ]
            )
        )
        items = await make_adapter().list_items(["4", "6"])
        sent = json.loads(route.calls.last.request.content)
        assert sent["method"] == "item.get"
        assert sent["params"]["hostids"] == ["4", "6"]
        assert "groupids" not in sent["params"]
        assert sent["params"]["filter"]["value_type"] == [0, 3]
        assert sent["params"]["selectHosts"] == ["hostid", "name"]
        assert items[0]["item_id"] == 42253
        assert items[0]["key"] == "system.cpu.util"  # key_ -> key
        assert items[0]["host"] == "web-01"
        assert items[0]["units"] == "%"

    @respx.mock
    async def test_value_type_override(self) -> None:
        route = respx.post(ENDPOINT).mock(return_value=rpc_ok([]))
        await make_adapter().list_items(["4"], value_types=[0])
        sent = json.loads(route.calls.last.request.content)
        assert sent["params"]["filter"]["value_type"] == [0]

    @respx.mock
    async def test_empty_host_ids_rejected(self) -> None:
        with pytest.raises(AdapterAPIError):
            await make_adapter().list_items([])


class TestListHosts:
    @respx.mock
    async def test_params_and_parse(self) -> None:
        route = respx.post(ENDPOINT).mock(
            return_value=rpc_ok(
                [
                    {
                        "hostid": "10554",
                        "name": "web-01",
                        # Zabbix 6.4+/7.x 用 hostgroups 字段（selectHostGroups）
                        "hostgroups": [{"groupid": "4", "name": "Linux servers"}],
                    }
                ]
            )
        )
        hosts = await make_adapter().list_hosts(["4"])
        sent = json.loads(route.calls.last.request.content)
        assert sent["method"] == "host.get"
        assert sent["params"]["groupids"] == ["4"]
        assert sent["params"]["selectHostGroups"] == ["groupid", "name"]
        assert hosts[0]["host_id"] == 10554
        assert hosts[0]["name"] == "web-01"
        assert hosts[0]["groups"] == [{"group_id": 4, "name": "Linux servers"}]

    @respx.mock
    async def test_empty_group_ids_rejected(self) -> None:
        with pytest.raises(AdapterAPIError):
            await make_adapter().list_hosts([])


class TestFetchTrendData:
    @respx.mock
    async def test_parse_numeric_strings(self) -> None:
        route = respx.post(ENDPOINT).mock(
            return_value=rpc_ok(
                [
                    {
                        "itemid": "42253",
                        "clock": "1756659600",
                        "num": "60",
                        "value_min": "12.3400",
                        "value_avg": "31.2250",
                        "value_max": "58.9000",
                    }
                ]
            )
        )
        trends = await make_adapter().fetch_trend_data(
            [42253], time_from=1756656000, time_till=1759334399
        )
        sent = json.loads(route.calls.last.request.content)
        assert sent["method"] == "trend.get"
        assert sent["params"]["itemids"] == ["42253"]
        assert sent["params"]["time_from"] == 1756656000
        row = trends[0]
        assert row["item_id"] == 42253 and row["clock"] == 1756659600
        assert row["value_avg"] == pytest.approx(31.225)
        assert row["value_max"] == pytest.approx(58.9)

    @respx.mock
    async def test_empty_result(self) -> None:
        respx.post(ENDPOINT).mock(return_value=rpc_ok([]))
        assert await make_adapter().fetch_trend_data([1], 0, 1) == []

    async def test_empty_item_ids_short_circuit(self) -> None:
        # 空 item_ids 不发请求
        assert await make_adapter().fetch_trend_data([], 0, 1) == []

    @respx.mock
    async def test_rpc_error(self) -> None:
        respx.post(ENDPOINT).mock(
            return_value=rpc_err(-32602, "Invalid params.", "Bad item id")
        )
        with pytest.raises(AdapterAPIError) as exc_info:
            await make_adapter().fetch_trend_data([999], 0, 1)
        assert exc_info.value.code == -32602


class TestTokenNotLeaked:
    @respx.mock
    async def test_token_not_in_logs_on_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        respx.post(ENDPOINT).mock(
            return_value=rpc_err(-32602, "Invalid params.", "Not authorized")
        )
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(AdapterAuthError):
                await make_adapter().test_connection()
        assert TOKEN not in caplog.text
        assert "Bearer" not in caplog.text

    @respx.mock
    async def test_token_not_in_logs_on_network_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        respx.post(ENDPOINT).mock(side_effect=httpx.ConnectError("refused"))
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(AdapterConnectionError):
                await make_adapter(max_retries=1).test_connection()
        assert TOKEN not in caplog.text
