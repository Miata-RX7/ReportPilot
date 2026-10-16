"""Zabbix 数据源 Adapter（Zabbix 7.2，JSON-RPC 2.0 over HTTP，Bearer token 认证）。

安全约束：token（self._secret）仅存于内存，禁止写入日志、异常消息、返回值。
"""

import asyncio
import logging
from typing import Any

import httpx

from app.config import get_settings
from app.services.adapters.base import (
    AdapterAPIError,
    AdapterAuthError,
    AdapterConnectionError,
    AdapterPermissionError,
    DataSourceAdapter,
)

logger = logging.getLogger(__name__)

# 数值型监控项：0 = numeric float, 3 = numeric unsigned（字符型无法做趋势聚合）
NUMERIC_VALUE_TYPES = [0, 3]


class ZabbixAdapter(DataSourceAdapter):
    """对接 Zabbix 7.2 API。

    config: {"url": "...", "timeout": 30, "verify_ssl": true, "max_retries": 2?}
    secret: API token 明文（由调用方解密后传入）。
    """

    def __init__(self, config: dict[str, Any], secret: str | None = None) -> None:
        super().__init__(config, secret)
        settings = get_settings()
        url = str(config["url"]).rstrip("/")
        self._endpoint = (
            url if url.endswith("api_jsonrpc.php") else f"{url}/api_jsonrpc.php"
        )
        self._timeout = int(config.get("timeout") or settings.zabbix_default_timeout)
        self._verify_ssl = bool(config.get("verify_ssl", True))
        self._max_retries = int(config.get("max_retries") or settings.zabbix_max_retries)
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _call(
        self, method: str, params: dict[str, Any], *, auth: bool = True
    ) -> Any:
        """执行一次 JSON-RPC 调用（含重试），返回 result 或抛出分类异常。

        auth=False 用于无需认证的方法：Zabbix 7.x 对携带 Authorization 头的
        apiinfo.version 会返回 -32602（"must be called without authorization
        header"），因此探测 URL 可达性时必须省略认证头。
        """
        if not self._secret:
            raise AdapterAuthError("数据源未配置 API token")

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self._next_id(),
        }
        headers: dict[str, str] = {"Content-Type": "application/json-rpc"}
        if auth:
            headers["Authorization"] = f"Bearer {self._secret}"

        resp: httpx.Response | None = None
        async with httpx.AsyncClient(
            timeout=self._timeout, verify=self._verify_ssl
        ) as client:
            for attempt in range(self._max_retries + 1):
                try:
                    resp = await client.post(
                        self._endpoint, json=payload, headers=headers
                    )
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                    # 网络层错误：重试后仍失败则报 URL 不可达
                    logger.warning(
                        "Zabbix 调用 %s 网络错误（第 %d 次）: %s",
                        method,
                        attempt + 1,
                        type(exc).__name__,
                    )
                    if attempt < self._max_retries:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise AdapterConnectionError(
                        f"Zabbix 地址不可达或超时: {type(exc).__name__}"
                    ) from exc

                if resp.status_code >= 500:
                    logger.warning(
                        "Zabbix 调用 %s HTTP %d（第 %d 次）",
                        method,
                        resp.status_code,
                        attempt + 1,
                    )
                    if attempt < self._max_retries:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise AdapterConnectionError(
                        f"Zabbix 服务端错误 HTTP {resp.status_code}"
                    )
                if resp.status_code in (401, 403):
                    raise AdapterAuthError(f"认证失败 HTTP {resp.status_code}")
                if resp.status_code != 200:
                    raise AdapterConnectionError(
                        f"Zabbix 返回非预期 HTTP {resp.status_code}"
                    )
                break

        assert resp is not None
        try:
            data = resp.json()
        except ValueError as exc:
            raise AdapterConnectionError("Zabbix 响应不是合法 JSON") from exc

        if "error" in data:
            err = data["error"]
            code: int | None = err.get("code")
            text = f"{err.get('message', '')} {err.get('data', '')}"
            # Zabbix 对无效/过期 token、权限不足均返回 -32602，靠 data 文案区分
            if any(k in text for k in ("Not authorized", "re-login", "Session terminated")):
                raise AdapterAuthError("API token 无效或已过期")
            if "No permissions" in text:
                raise AdapterPermissionError(f"权限不足: {err.get('data', '')}")
            raise AdapterAPIError(code, text.strip())

        logger.info("Zabbix 调用 %s 成功", method)
        return data.get("result")

    async def test_connection(self) -> bool:
        """先 apiinfo.version 验证 URL 可达（不带认证头），再 hostgroup.get 验证 token。"""
        version = await self._call("apiinfo.version", {}, auth=False)
        await self._call("hostgroup.get", {"output": ["groupid"], "limit": 1})
        logger.info("Zabbix 连通性测试通过，版本 %s", version)
        return True

    async def list_host_groups(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await self._call(
            "hostgroup.get",
            {"output": ["groupid", "name"], "sortfield": "name"},
        )
        return [
            {"group_id": int(g["groupid"]), "name": g["name"]} for g in result
        ]

    async def list_hosts(self, group_ids: list[str]) -> list[dict[str, Any]]:
        """按主机组列出主机。

        Zabbix 6.4+/7.x 中 host.get 取所属组必须用 selectHostGroups；
        旧参数 selectGroups 会静默返回空数组。
        """
        if not group_ids:
            raise AdapterAPIError(None, "list_hosts 需要 group_ids")
        result: list[dict[str, Any]] = await self._call(
            "host.get",
            {
                "output": ["hostid", "name"],
                "groupids": [str(g) for g in group_ids],
                "selectHostGroups": ["groupid", "name"],
                "sortfield": "name",
            },
        )
        return [
            {
                "host_id": int(h["hostid"]),
                "name": h["name"],
                "groups": [
                    {"group_id": int(g["groupid"]), "name": g["name"]}
                    for g in (h.get("hostgroups") or [])
                ],
            }
            for h in result
        ]

    async def list_items(
        self,
        host_ids: list[str],
        value_types: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """按主机列出监控项，默认只取数值型（value_type 0/3）。

        value_types 可传 query_config.value_type_override 覆盖默认过滤。
        """
        if not host_ids:
            raise AdapterAPIError(None, "list_items 需要 host_ids")
        params: dict[str, Any] = {
            "output": ["itemid", "name", "key_", "hostid", "value_type", "units"],
            "selectHosts": ["hostid", "name"],
            "hostids": [str(h) for h in host_ids],
            "filter": {"value_type": value_types or NUMERIC_VALUE_TYPES},
            "sortfield": "name",
        }
        result: list[dict[str, Any]] = await self._call("item.get", params)
        return [
            {
                "item_id": int(i["itemid"]),
                "name": i["name"],
                "key": i["key_"],
                "host_id": int(i["hostid"]),
                "host": (i.get("hosts") or [{}])[0].get("name", ""),
                "value_type": int(i["value_type"]),
                "units": i.get("units", ""),
            }
            for i in result
        ]

    async def fetch_latest_value(
        self,
        item_id: int,
        time_from: int | None = None,
        time_till: int | None = None,
    ) -> dict[str, Any] | None:
        """history.get limit=1 取最新一条原始采样值（text mode=latest 用）。

        sortfield=clock / sortorder=DESC 保证是「最新」；不传 time_till，
        取服务端保留的全部历史中的最后一条。

        ``time_from`` / ``time_till`` 是为 AIOps「周期末点」语义预留的可选区间，
        **Zabbix 刻意忽略**（仍然取全历史最后一条），保持升级前行为完全一致。
        """
        result: list[dict[str, Any]] = await self._call(
            "history.get",
            {
                "output": ["itemid", "clock", "value", "units"],
                "history": 1,
                "itemids": [str(item_id)],
                "sortfield": "clock",
                "sortorder": "DESC",
                "limit": 1,
            },
        )
        if not result:
            return None
        row = result[0]
        return {
            "item_id": int(row["itemid"]),
            "clock": int(row["clock"]),
            "value": str(row["value"]),
            "units": row.get("units", ""),
        }

    async def get_item_meta(self, item_id: int) -> dict[str, Any] | None:
        """item.get 取 value_type 与 valuemap（pie 适合性判定用）。

        Zabbix 6.0+ 用 `selectValueMap: [valuemapid, name, mappings]`，结果落在
        `valuemaps[0].mappings`（{原始值: 文本}）；旧版（<6.0）用 `valuemap` 字段。
        两种都兼容，缺失时按空映射处理。
        """
        result: list[dict[str, Any]] = await self._call(
            "item.get",
            {
                "output": ["itemid", "name", "key_", "value_type", "units", "valuemapid"],
                "selectValueMap": ["valuemapid", "name", "mappings"],
                "itemids": [str(item_id)],
            },
        )
        if not result:
            return None
        it = result[0]
        # Zabbix 6.0+ 实测形状：
        #   valuemap = {"valuemapid": "...", "name": "...", "mappings": [{type,value,newvalue}, ...]}
        # 旧版（<6.0）则是 {value: text} 字典。两种都归一为 {原始值: 文本}。
        valuemap: dict[str, str] = {}
        raw_vm = it.get("valuemap")
        if isinstance(raw_vm, dict):
            mappings = raw_vm.get("mappings")
            if isinstance(mappings, list):
                for m in mappings:
                    if isinstance(m, dict) and "value" in m:
                        valuemap[str(m["value"])] = str(m.get("newvalue", ""))
            else:
                valuemap = {str(k): str(v) for k, v in raw_vm.items()}
        return {
            "item_id": int(it["itemid"]),
            "name": it.get("name", ""),
            "key": it.get("key_", ""),
            "value_type": int(it["value_type"]),
            "units": it.get("units", ""),
            "valuemap": valuemap,
        }

    async def fetch_trend_data(
        self,
        item_ids: list[int],
        time_from: int,
        time_till: int,
    ) -> list[dict[str, Any]]:
        """trend.get 拉取小时级趋势数据，透传 min/avg/max 三列。

        二次聚合语义（calc_engine 约定，本期与基线必须用同一 agg 列）：
        - agg=avg -> 对每小时 value_avg 再取平均
        - agg=max -> 对每小时 value_max 再取最大
        - agg=sum -> 对每小时 value_avg 求和

        区间语义修正：调用方传入的 [time_from, time_till] 是「左闭右开」的日历月
        区间（time_till = 次月首日 00:00）。Zabbix trend 的小时桶以「桶起始时刻」
        作为 clock，桶覆盖 [clock, clock+3600)；当桶边界恰与 time_till 重合时，
        该桶（属于下一周期）会被 Zabbix 判为落在区间内并计入本期，导致本期多算
        约 1 小时。这里把 time_till 回退 1 秒，使区间严格等价于 [start, end)，
        保持与调用方及既有测试一致的口径。
        """
        if not item_ids:
            return []
        params: dict[str, Any] = {
            "output": ["itemid", "clock", "num", "value_min", "value_avg", "value_max"],
            "itemids": [str(i) for i in item_ids],
            "time_from": time_from,
            "time_till": max(time_from, time_till - 1),
        }
        result: list[dict[str, Any]] = await self._call("trend.get", params)
        return [
            {
                "item_id": int(t["itemid"]),
                "clock": int(t["clock"]),
                "num": int(t["num"]),
                "value_min": float(t["value_min"]),
                "value_avg": float(t["value_avg"]),
                "value_max": float(t["value_max"]),
            }
            for t in result
        ]
