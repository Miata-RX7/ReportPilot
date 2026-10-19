"""Zabbix / AIOps 两个 Adapter 的列表方法确实接上了缓存。

`test_adapter_list_cache.py` 测的是缓存助手本身；这里测的是「六个 list_* 方法
真的包了它」，否则改了助手但忘了接方法，测试依然全绿。
"""

import httpx
import pytest
import respx

from app.services.adapters.base import clear_list_cache
from app.services.adapters.zabbix_adapter import ZabbixAdapter

ZABBIX_URL = "https://zabbix.test"


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_list_cache()
    yield
    clear_list_cache()


def _zabbix(cache_ns=None) -> ZabbixAdapter:
    return ZabbixAdapter({"url": ZABBIX_URL}, "token", cache_ns)




def _rpc(result):
    return {"jsonrpc": "2.0", "result": result, "id": 1}


    return {"code": 0, "message": "ok", "data": data}


class TestZabbixCaching:
    @respx.mock
    async def test_host_groups_cached(self) -> None:
        route = respx.post(f"{ZABBIX_URL}/api_jsonrpc.php").mock(
            return_value=httpx.Response(200, json=_rpc([{"groupid": "4", "name": "G"}]))
        )
        a = _zabbix(1)
        assert await a.list_host_groups() == [{"group_id": 4, "name": "G"}]
        assert await a.list_host_groups() == [{"group_id": 4, "name": "G"}]
        assert route.call_count == 1, "第二次应命中缓存"

    @respx.mock
    async def test_hosts_cached_and_key_order_insensitive(self) -> None:
        route = respx.post(f"{ZABBIX_URL}/api_jsonrpc.php").mock(
            return_value=httpx.Response(
                200, json=_rpc([{"hostid": "10", "name": "h", "hostgroups": []}])
            )
        )
        a = _zabbix(2)
        await a.list_hosts(["4", "7"])
        await a.list_hosts(["7", "4"])  # 顺序不同但集合相同
        assert route.call_count == 1, "group_ids 顺序不同不应拆成两个缓存条目"

    @respx.mock
    async def test_items_cache_key_includes_value_types(self) -> None:
        route = respx.post(f"{ZABBIX_URL}/api_jsonrpc.php").mock(
            return_value=httpx.Response(
                200,
                json=_rpc(
                    [{"itemid": "1", "name": "i", "key_": "k", "hostid": "10", "value_type": "0"}]
                ),
            )
        )
        a = _zabbix(3)
        await a.list_items(["10"])  # 默认数值型
        await a.list_items(["10"])  # 同口径 -> 命中
        assert route.call_count == 1
        await a.list_items(["10"], value_types=[3, 0])  # 不同口径 -> 回源
        assert route.call_count == 2, "不同 value_type 过滤不能共用缓存"

    @respx.mock
    async def test_two_datasources_do_not_share_cache(self) -> None:
        route = respx.post(f"{ZABBIX_URL}/api_jsonrpc.php").mock(
            return_value=httpx.Response(200, json=_rpc([{"groupid": "4", "name": "G"}]))
        )
        await _zabbix(100).list_host_groups()
        await _zabbix(200).list_host_groups()  # 不同数据源 id
        assert route.call_count == 2, "不同数据源必须各自回源"


