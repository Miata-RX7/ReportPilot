"""AIOps 数据源 Adapter（REST + header token 鉴权，非 JSON-RPC）。

接口形态与 Zabbix 差别很大，三级浏览的语义映射：

====================== ====================== ==========================
抽象方法AIOps 接口
====================== ====================== ==========================
list_host_groups       /monitor/model/base/list模型（monitor_switch=true）
list_hosts             /cmdb/model/instance/search           实例
list_items             /monitor/instance/item/list            实例的监控项
list_item_rows         /monitor/instance/item/status         list 型的主键行
fetch_trend_data       /monitor/instance/item/history曲线点（原始点，需自行分桶）
fetch_latest_value     同上 / status                文本值（周期末点，缺失时回退当前值）
====================== ====================== ==========================

**实测得到的关键约束（2026-10-16，全部有据可查，勿凭直觉改）**：

1. ``limit`` 无上限，模型 / 实例可一次拉全 —— 不做分页
2. ``search_unit_list`` **不能预先 URL 编码**：传 ``%5B%5D`` 会返回 code=2；
   传原始 ``[]`` 或**直接省略**都正常。本实现选择省略
3. ``item_id_list`` 必须是**带方括号的字面量字符串** ``"[9669]"``，
   传裸数字报 ``[]int64: decode slice: expect [ or n``
4. ``status`` 返回的 ``data`` 字段是**二次编码的 JSON 字符串**（需 loads 两次）
5. list 型监控项取某一行某列的曲线，**必须**同时带 ``data_source_more``（行键）
   与 ``tags``（``{"主键列id": "行键"}``）；否则接口返回的是**跨行聚合值**，
   不报错、也不报错，但数值是错的 —— 实测踩过
6. 曲线点的间隔在 57~63 秒之间抖动，**不按整点对齐**，
   所以分桶必须 ``unix // 3600 * 3600``，不能假设桶已对齐
7. 接口返回的 ``avg``/``max``/``min`` 是**整个请求窗口**的统计，
   不是分桶的 —— 每小时的 min/avg/max 必须自己算
8. 「某行该列当前值非空」⟺「该行该列有历史」（26 行逐行实测，零误判零漏判），
   因此行列表可以直接按当前值过滤，不必逐行探测历史

安全约束：token（self._secret）仅存于内存，禁止写入日志、异常消息、返回值。
"""

import asyncio
import itertools
import json
import logging
import threading
import time
from typing import Any

import httpx

from app.config import get_settings
from app.services.adapters.base import (
    AdapterAPIError,
    AdapterAuthError,
    AdapterConnectionError,
    DataSourceAdapter,
)
from app.services.aiops_keys import (
    AIOpsColumn,
    decode_ref,
    parse_columns,
)

logger = logging.getLogger(__name__)

# 单次请求的防御性上限（实测 limit 无上限，但不能让服务端无限返回）
PAGE_LIMIT = 2000
# status 表缓存 TTL（秒）：端口表动辄几百行，展开一次下拉不该每次都拉一遍
STATUS_CACHE_TTL = 60.0
# 监控项元信息缓存 TTL（秒）：取曲线时需要 primary_ids，不能每次都拉 item/list
ITEM_META_TTL = 300.0

# --------------------------------------------------------------------------
# 定位串 <-> 合成整数
#
# 为什么需要：calc_engine 内部对 item_id 做 int() 并用 dict[int, ...] 分组，
# 而 AIOps 的坐标里含字符串主键行，无法编码成整数。
#
# 为什么可以用模块级字典而不用担心无限增长：注册只发生在 normalize_item_keys，
# 而它只被喂 query_config 里**已配置的**定位串 —— 增长上限是配置规模，
# 与请求量无关（曲线配 12 条就是 12 个键，不会因为跑了 1000 次报告变成 12000个）。
# --------------------------------------------------------------------------
_KEY_REGISTRY: dict[str, int] = {}
_KEY_REVERSE: dict[int, str] = {}
_KEY_LOCK = threading.Lock()
_KEY_SEQ = itertools.count(1)

# 实例搜索的服务端参数名未在官方样本中确认：先试，不支持则本地过滤并记住结论
_SEARCH_PARAM: str | None = None
_SEARCH_PARAM_OK: bool | None = None

_STATUS_CACHE: dict[tuple[int, int], tuple[float, dict[str, list[str]], int]] = {}
_ITEM_META_CACHE: dict[tuple[int, int], tuple[float, dict[str, Any]]] = {}
#: 模型 id -> 名称。list_hosts 要把「模型」塞进每个实例的 groups 里，
#: 而模型名只有 list_host_groups 才拿得到，所以顺手缓存一份，省掉一次 RPC。
_MODEL_NAMES: dict[int, str] = {}
_MODEL_NAMES_AT: float = 0.0
_CACHE_LOCK = threading.Lock()


def _register_key(key: str) -> int:
    """定位串 -> 合成整数（进程内稳定，首次见到时分配）。"""
    with _KEY_LOCK:
        got = _KEY_REGISTRY.get(key)
        if got is None:
            got = next(_KEY_SEQ)
            _KEY_REGISTRY[key] = got
            _KEY_REVERSE[got] = key
        return got


def _lookup_key(synthetic_id: int) -> str:
    """合成整数 -> 定位串。取不到说明没走 normalize，属调用顺序错误。"""
    with _KEY_LOCK:
        got = _KEY_REVERSE.get(synthetic_id)
    if got is None:
        raise AdapterAPIError(
            None, "AIOps 定位串未注册（须先经 normalize_item_keys 归一）"
        )
    return got


def _empty_hours() -> dict[int, list[float]]:
    return {}


def bucket_hourly(
    nodes: list[dict[str, Any]], synthetic_id: int
) -> list[dict[str, Any]]:
    """把原始采样点按小时分桶，产出 calc_engine 约定的行形状。

    必须自己分桶的原因见模块 docstring 第6、7 条：点不按整点对齐，
    且接口给的 avg/max/min 是整窗统计而非分桶统计。

    桶键用 ``unix - unix % 3600``（等价于 ``unix // 3600 * 3600``），
    保证同一小时内所有点落进同一桶；**不假设**采样点对齐在整点。
    """
    buckets: dict[int, list[float]] = _empty_hours()
    for node in nodes or []:
        ts = node.get("unix")
        if ts is None:
            continue
        raw = node.get("value")
        if raw is None or raw == "":
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        ts = int(ts)
        buckets.setdefault(ts - (ts % 3600), []).append(value)

    rows: list[dict[str, Any]] = []
    for clock in sorted(buckets):
        vals = buckets[clock]
        count = len(vals)
        rows.append(
            {
                "item_id": synthetic_id,
                "clock": clock,
                "num": count,
                "value_min": min(vals),
                "value_avg": sum(vals) / count,
                "value_max": max(vals),
            }
        )
    return rows


class AIOpsAdapter(DataSourceAdapter):
    """对接 AIOps 平台。

    config: ``{"url": "https://aiops.livecom.ltd", "timeout": 30, "verify_ssl": true}``
    secret: token 明文（调用方解密后传入），请求头 ``token: <secret>``
    """

    scope_kind = "model_instance"

    def __init__(self, config: dict[str, Any], secret: str | None = None) -> None:
        super().__init__(config, secret)
        settings = get_settings()
        url = str(config["url"]).rstrip("/")
        # 允许配置到 /api 或只给站点根，两种都归一
        self._base = url if url.endswith("/api") else f"{url}/api"
        self._timeout = int(config.get("timeout") or settings.zabbix_default_timeout)
        self._verify_ssl = bool(config.get("verify_ssl", True))
        self._max_retries = int(config.get("max_retries") or settings.zabbix_max_retries)

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    async def _get(self, path: str, **params: Any) -> Any:
        """GET + 解包 ``{code, message, data}``；code != 0 抛分类异常。

        token 只出现在请求头里，**任何日志/异常消息都不带它**。
        """
        if not self._secret:
            raise AdapterAuthError("数据源未配置 API token")
        headers = {"token": self._secret, "content-type": "application/json"}

        resp: httpx.Response | None = None
        async with httpx.AsyncClient(
            timeout=self._timeout, verify=self._verify_ssl
        ) as client:
            for attempt in range(self._max_retries + 1):
                try:
                    resp = await client.get(
                        f"{self._base}{path}", params=params, headers=headers
                    )
                except (
                    httpx.ConnectError,
                    httpx.ConnectTimeout,
                    httpx.ReadTimeout,
                ) as exc:
                    logger.warning(
                        "AIOps %s 网络错误（第 %d 次）: %s",
                        path,
                        attempt + 1,
                        type(exc).__name__,
                    )
                    if attempt < self._max_retries:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise AdapterConnectionError(
                        f"AIOps 地址不可达或超时: {type(exc).__name__}"
                    ) from exc

                if resp.status_code >= 500:
                    logger.warning(
                        "AIOps %s HTTP %d（第 %d 次）",
                        path,
                        resp.status_code,
                        attempt + 1,
                    )
                    if attempt < self._max_retries:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise AdapterConnectionError(
                        f"AIOps 服务端错误 HTTP {resp.status_code}"
                    )
                if resp.status_code in (401, 403):
                    raise AdapterAuthError(f"认证失败 HTTP {resp.status_code}")
                if resp.status_code != 200:
                    raise AdapterConnectionError(
                        f"AIOps 返回非预期 HTTP {resp.status_code}"
                    )
                break

        assert resp is not None
        try:
            data = resp.json()
        except ValueError as exc:
            raise AdapterConnectionError("AIOps 响应不是合法 JSON") from exc

        code = data.get("code")
        if code != 0:
            message = str(data.get("message") or "")
            # code=9 是未带/无效 token（实测），文案也可能是 auth failed
            if code == 9 or "auth" in message.lower():
                raise AdapterAuthError("API token 无效或已过期")
            raise AdapterAPIError(code, message or f"AIOps 返回 code={code}")
        return data.get("data") or {}

    async def test_connection(self) -> bool:
        """拉一个模型验证连通性与 token（不选监控开关，1 条足够）。"""
        await self._get("/monitor/model/base/list", offset=0, limit=1)
        logger.info("AIOps 连通性测试通过")
        return True

    # ------------------------------------------------------------------
    # 三级浏览：模型 -> 实例 -> 监控项
    # ------------------------------------------------------------------
    async def list_host_groups(self) -> list[dict[str, Any]]:
        """列出**开启了监控且有实例**的模型。

        两个过滤缺一不可（实测）：
        - ``monitor_switch=false`` 的模型没有监控项
        - ``monitor_switch=true`` 但 ``instance_num=0`` 的模型照样是空的
          （堡垒机 / IDS / IPS / 扫描仪 / VPN 等都是这类），不过滤会让用户
          选到一个点开什么都没有的模型
        """
        data = await self._get(
            "/monitor/model/base/list", offset=0, limit=PAGE_LIMIT
        )
        out: list[dict[str, Any]] = []
        with _CACHE_LOCK:
            _MODEL_NAMES.clear()
            _MODEL_NAMES_AT = time.monotonic()
        for row in data.get("list") or []:
            with _CACHE_LOCK:
                _MODEL_NAMES[int(row["id"])] = str(row.get("name") or row["id"])
            if not row.get("monitor_switch"):
                continue
            instance_num = int(row.get("instance_num") or 0)
            if instance_num <= 0:
                continue
            out.append(
                {
                    "group_id": int(row["id"]),
                    "name": str(row.get("name") or row["id"]),
                    "instance_num": instance_num,
                    "template_num": int(row.get("template_num") or 0),
                }
            )
        out.sort(key=lambda r: r["name"])
        return out

    async def list_hosts(
        self, group_ids: list[str], q: str | None = None
    ) -> list[dict[str, Any]]:
        """按模型列出实例。

        实例搜索参数名未在接口样本中确认，故策略是「先试服务端、失败则本地过滤并记住」
        （见 ``_SEARCH_PARAM_OK``），避免把未验证的参数名写死。
        """
        if not group_ids:
            raise AdapterAPIError(None, "list_hosts 需要 model_ids")

        out: list[dict[str, Any]] = []
        for raw_mid in group_ids:
            try:
                mid = int(raw_mid)
            except (TypeError, ValueError):
                raise AdapterAPIError(None, f"model_id 非法: {raw_mid!r}") from None
            rows = await self._search_instances(mid, q)
            for row in rows:
                show = row.get("show_attr") or {}
                out.append(
                    {
                        "host_id": int(row["id"]),
                        # show_title 实测是实例名（如 缅甸Mytel-10.100.100.251-ASW），
                        # 比 attr.hostname 更适合人读；show_attr.ip 恒为字符串
                        "name": str(
                            row.get("show_title")
                            or show.get("hostname")
                            or row["id"]
                        ),
                        "ip": str(show.get("ip") or ""),
                        # 必须带 name：api 层 _to_group_refs 直接取 r["name"]，
                        # 缺了就是 KeyError -> 500（实测踩过）。
                        # 名字从模型缓存取；缓存空时先补拉一次模型列表。
                        "groups": [
                            {"group_id": mid, "name": await _model_name(self, mid)}
                        ],
                    }
                )
        if q and _SEARCH_PARAM_OK is False:
            # 服务端不支持搜索时的本地兜底
            needle = q.strip().lower()
            out = [
                r
                for r in out
                if needle in str(r["name"]).lower()
                or needle in str(r.get("ip", "")).lower()
            ]
        out.sort(key=lambda r: r["name"])
        return out

    async def _search_instances(
        self, model_id: int, q: str | None
    ) -> list[dict[str, Any]]:
        """调 instance/search。

        **刻意不传 ``search_unit_list``**：实测省略即正常；传原始 ``[]`` 也正常，
        但传预先 URL 编码的 ``%5B%5D`` 会返回 code=2（客户端会再编码一次）。

        服务端搜索的参数名未出现在接口样本里，因此**首次探测**候选名，
        全部不通就永久降级为本地过滤（并记进模块级状态，不重复试错）。
        """
        global _SEARCH_PARAM, _SEARCH_PARAM_OK

        base: dict[str, Any] = {
            "offset": 0,
            "limit": PAGE_LIMIT,
            "model_id": model_id,
        }
        path = "/cmdb/model/instance/search"

        if not q or _SEARCH_PARAM_OK is False:
            data = await self._get(path, **base)
            return list(data.get("list") or [])

        if _SEARCH_PARAM is None:
            for candidate in ("search", "keyword", "name"):
                try:
                    data = await self._get(path, **{**base, candidate: q})
                except AdapterAPIError:
                    continue
                _SEARCH_PARAM, _SEARCH_PARAM_OK = candidate, True
                logger.info("AIOps 实例服务端搜索参数确定为 %s", candidate)
                return list(data.get("list") or [])
            _SEARCH_PARAM_OK = False
            logger.warning(
                "AIOps instance/search 无可用服务端搜索参数，改为本地过滤"
            )
            data = await self._get(path, **base)
            return list(data.get("list") or [])

        data = await self._get(path, **{**base, _SEARCH_PARAM: q})
        return list(data.get("list") or [])

    async def list_items(
        self,
        host_ids: list[str],
        value_types: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """按实例列出监控项，并带上 AIOps 独有的列元数据。

        ``list_items`` 的返回被 ``ItemRef`` 逐字段取用，这里多出来的
        ``aiops_*`` 键由 ``api/datasources.py`` 映射到可选字段。

        注意单位在 AIOps 是**列级**的（端口表的每一列单位不同），
        所以 ``units`` 恒为空串，真正的单位在 ``aiops_columns[].unit``。
        """
        if not host_ids:
            raise AdapterAPIError(None, "list_items 需要 instance_ids")

        out: list[dict[str, Any]] = []
        for raw_iid in host_ids:
            try:
                iid = int(raw_iid)
            except (TypeError, ValueError):
                raise AdapterAPIError(None, f"instance_id 非法: {raw_iid!r}") from None
            data = await self._get(
                "/monitor/instance/item/list",
                instance_id=iid,
                offset=0,
                limit=PAGE_LIMIT,
            )
            for it in data.get("list") or []:
                dt = str(it.get("data_type") or "")
                columns: list[AIOpsColumn] = (
                    parse_columns(it) if dt == "list" else []
                )
                out.append(
                    {
                        "item_id": str(it["item_id"]),
                        "name": str(it.get("name") or ""),
                        "key": str(it.get("name_en") or ""),
                        "host_id": str(iid),
                        "host": "",
                        # value_type 是 Zabbix 的取值空间，平台仅用于「是否数值」判断；
                        # AIOps 的真实类型在 aiops_data_type，两边保持一致即可
                        "value_type": 0 if dt == "number" else 3,
                        "units": "",
                        "aiops_data_type": dt,
                        "aiops_columns": [c.__dict__ for c in columns],
                        "aiops_template_id": data.get("template_id"),
                        "aiops_template_name": str(
                            data.get("template_name") or ""
                        ),
                        "aiops_cycle": int(it.get("cycle") or 0),
                    }
                )
        return out

    # ------------------------------------------------------------------
    # 归一：定位串 -> 合成整数
    # ------------------------------------------------------------------
    async def normalize_item_keys(self, keys: list[str]) -> dict[str, int]:
        """把 query_config 里的定位串换成 calc_engine 能用的整数 id。

        非法定位串直接抛 AdapterAPIError（调用方转 422）—— 静默跳过会让指标
        在保存时通过、试算时才炸，且错误信息离现场很远。
        """
        out: dict[str, int] = {}
        for key in keys:
            try:
                decode_ref(key)
            except ValueError as exc:
                raise AdapterAPIError(None, str(exc)) from None
            out[key] = _register_key(key)
        return out

    # ------------------------------------------------------------------
    # 带缓存的内部读取
    # ------------------------------------------------------------------
    async def _item_meta(self, instance_id: int, item_id: int) -> dict[str, Any]:
        """单个监控项的原始元信息（含 primary_ids / source / source_*）。

        取曲线时需要 primary_ids 才能拼 ``tags``，不能每次都拉整份 item/list，
        故按 (instance, item) 缓存。
        """
        ck = (instance_id, item_id)
        now = time.monotonic()
        with _CACHE_LOCK:
            hit = _ITEM_META_CACHE.get(ck)
            if hit and now - hit[0] < ITEM_META_TTL:
                return hit[1]
        data = await self._get(
            "/monitor/instance/item/list",
            instance_id=instance_id,
            offset=0,
            limit=PAGE_LIMIT,
        )
        meta: dict[str, Any] = {}
        for it in data.get("list") or []:
            if int(it.get("item_id") or 0) == item_id:
                meta = it
                break
        with _CACHE_LOCK:
            _ITEM_META_CACHE[ck] = (now, meta)
        return meta

    async def _status_table(
        self, instance_id: int, item_id: int
    ) -> tuple[dict[str, list[str]], int]:
        """list 型监控项的当前值表，按列 id -> 行值列表返回。

        - ``item_id_list`` **必须**是带方括号的字面量字符串（实测）
        - ``data`` 字段是**二次编码的 JSON 字符串**，需要 loads 两次
        - 大设备的端口表有几百行，缓存 STATUS_CACHE_TTL 秒
        """
        ck = (instance_id, item_id)
        now = time.monotonic()
        with _CACHE_LOCK:
            hit = _STATUS_CACHE.get(ck)
            if hit and now - hit[0] < STATUS_CACHE_TTL:
                return hit[1], int(hit[2])

        data = await self._get(
            "/monitor/instance/item/status",
            instance_id=instance_id,
            from_instance_id=instance_id,
            item_id_list=f"[{item_id}]",
        )
        node = ((data.get("list") or {}).get(str(item_id))) or {}
        table: dict[str, list[str]] = {}
        raw = node.get("data")
        if isinstance(raw, str) and raw.strip():
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                decoded = {}
            if isinstance(decoded, dict):
                table = {str(k): list(v) for k, v in decoded.items() if isinstance(v, list)}
        ts = int(node.get("unix") or 0)
        with _CACHE_LOCK:
            _STATUS_CACHE[ck] = (now, table, ts)
        return table, ts

    async def resolve_scoped_items(
        self,
        *,
        upper_ids: list[str],
        lower_ids: list[str],
        keys: list[str],
    ) -> list[dict[str, Any]]:
        """AIOps 语义：上级是模型、下级是实例，定位串是四元组。

        与默认实现的三点差异：
        1. 定位串要拆成 ``(实例, 监控项, 列, 行)``，**存在性校验要按拆出来的
           监控项 id 与实例去做**，不能拿整串去比（默认实现是整串等于 item_id）
        2. 返回的元信息里带上该定位串对应的实例名与模型名，供 object / formula
           生成「模型X / 实例Y / 监控项Z」这类人类可读证据
        3. 曲线类定位串按 ``item_id`` 去重后再查 item/list，避免同一监控项被重复请求
        """
        hosts = await self.list_hosts(upper_ids)
        valid = {int(h["host_id"]) for h in hosts}
        wanted_lower = {int(x) for x in lower_ids if str(x).strip()}
        unknown = sorted(wanted_lower - valid)
        if unknown:
            raise AdapterAPIError(
                None, f"以下实例不属于所选模型 {upper_ids}: {unknown}"
            )

        refs: list[Any] = []
        for key in keys:
            try:
                refs.append(decode_ref(key))
            except ValueError as exc:
                raise AdapterAPIError(None, str(exc)) from None

        by_instance: dict[int, list[Any]] = {}
        for ref in refs:
            by_instance.setdefault(ref.instance_id, []).append(ref)

        meta_cache: dict[int, dict[int, dict[str, Any]]] = {}
        for instance_id in sorted(by_instance):
            data = await self._get(
                "/monitor/instance/item/list",
                instance_id=instance_id,
                offset=0,
                limit=PAGE_LIMIT,
            )
            meta_cache[instance_id] = {
                int(it["item_id"]): it for it in (data.get("list") or [])
            }

        out: list[dict[str, Any]] = []
        for key, ref in zip(keys, refs):
            if ref.instance_id not in meta_cache:
                raise AdapterAPIError(
                    None, f"实例 {ref.instance_id} 不在所选范围内"
                )
            item = meta_cache[ref.instance_id].get(ref.item_id)
            if item is None:
                raise AdapterAPIError(
                    None,
                    f"监控项 {ref.item_id} 未在实例 {ref.instance_id} 上找到",
                )
            out.append(
                {
                    "item_id": _register_key(key),
                    "name": str(item.get("name") or ""),
                    "key": str(item.get("name_en") or ""),
                    "host_id": str(ref.instance_id),
                    "host": str(
                        next(
                            (
                                h["name"]
                                for h in hosts
                                if int(h["host_id"]) == ref.instance_id
                            ),
                            "",
                        )
                    ),
                    "units": "",
                    "aiops_ref": ref,
                    "aiops_column": _column_of(item, ref.column_id),
                }
            )
        return out

    async def list_item_rows(
        self,
        *,
        instance_id: str,
        item_id: str,
        column_id: str,
    ) -> list[dict[str, Any]]:
        """列出主键行，并**过滤掉该列当前值为空的行**。

        过滤依据是实测结论「当前值非空 ⟺ 有历史」（26 行逐行验证，零误判零漏判）。
        不做这个过滤，级联里会出现大量「选了没数据」的选项 —— 用户要等报告生成后
        才发现曲线是空的。
        """
        iid, cid, col = int(instance_id), int(item_id), int(column_id)
        table, ts = await self._status_table(iid, cid)
        if not table:
            return []

        meta = await self._item_meta(iid, cid)
        primary_id = 0
        try:
            ids = (json.loads(meta.get("module_extension") or "{}").get("primary_ids")) or []
            primary_id = int(ids[0]) if ids else 0
        except (json.JSONDecodeError, TypeError, ValueError, IndexError):
            primary_id = 0
        # 没解析出主键列时退化为「行数最多的那一列」，保证列表不至于空掉
        if not primary_id:
            primary_id = max(table, key=lambda k: len(table[k]), default="")

        names = table.get(str(primary_id)) or []
        values = table.get(str(col)) or []
        rows: list[dict[str, Any]] = []
        for idx, name in enumerate(names):
            current = values[idx] if idx < len(values) else ""
            if str(current).strip() == "":
                continue
            rows.append(
                {"row_key": str(name), "current_value": str(current), "as_of": ts}
            )
        return rows

    # ------------------------------------------------------------------
    # 取数
    # ------------------------------------------------------------------
    async def get_item_meta(self, item_id: int) -> dict[str, Any] | None:
        """AIOps 不支持 pie（没有 Zabbix 那种 up/down 值域语义），显式拒绝。"""
        raise AdapterAPIError(None, "AIOps 数据源不支持 pie 类型指标")

    async def fetch_latest_value(
        self,
        item_id: int,
        time_from: int | None = None,
        time_till: int | None = None,
    ) -> dict[str, Any] | None:
        """取文本值：优先取**区间内最后一个点**，没有历史才回退到当前值。

        实测 AIOps 的 text 列（含 list 的 text 列）同样有历史，
        所以月报的 text 指标应给「周期末点」而不是「跑报告那一刻的当前值」——
        后者会让报告重跑一次就变一次，无法复核。

        ``time_from`` / ``time_till`` 为 None 时（老调用方）退化为纯当前值语义。
        """
        ref = decode_ref(_lookup_key(int(item_id)))

        if time_from is not None and time_till is not None:
            rows = await self._fetch_one(ref, int(item_id), int(time_from), int(time_till))
            if rows:
                last = rows[-1]
                value = last.get("value_avg")
                return {
                    "item_id": int(item_id),
                    "clock": last["clock"],
                    "value": _format_scalar(value),
                    "units": await self._column_unit(ref),
                }

        # 回退：list 列读 status 当前值
        if ref.is_list_column:
            rows = await self.list_item_rows(
                instance_id=str(ref.instance_id),
                item_id=str(ref.item_id),
                column_id=str(ref.column_id),
            )
            for row in rows:
                if row["row_key"] == ref.row_key:
                    return {
                        "item_id": int(item_id),
                        "clock": int(row.get("as_of") or 0),
                        "value": str(row["current_value"]),
                        "units": await self._column_unit(ref),
                    }
            return None

        meta = await self._item_meta(ref.instance_id, ref.item_id)
        data = await self._get(
            "/monitor/instance/item/status",
            instance_id=ref.instance_id,
            from_instance_id=ref.instance_id,
            item_id_list=f"[{ref.item_id}]",
        )
        node = ((data.get("list") or {}).get(str(ref.item_id))) or {}
        raw = node.get("data")
        if isinstance(raw, str) and raw.strip():
            try:
                table = json.loads(raw)
            except json.JSONDecodeError:
                return None
            sources = _first_source(meta)
            if isinstance(table, dict) and sources:
                values = table.get(str(sources[0])) or []
                if values:
                    return {
                        "item_id": int(item_id),
                        "clock": int(node.get("unix") or 0),
                        "value": str(values[0]),
                        "units": str(meta.get("units") or ""),
                    }
        return None

    async def _column_unit(self, ref: Any) -> str:
        meta = await self._item_meta(ref.instance_id, ref.item_id)
        for col in parse_columns(meta):
            if ref.column_id is None or col.column_id == ref.column_id:
                return col.unit
        return ""

    async def fetch_trend_data(
        self,
        item_ids: list[int],
        time_from: int,
        time_till: int,
    ) -> list[dict[str, Any]]:
        """逐个定位串取曲线并按小时分桶。

        区间语义与 Zabbix 侧保持一致：调用方给的是**左闭右开**的日历月区间，
        这里把 time_till 回退 1 秒（节点自带自身时间戳，严格落在 [start, end)）。
        """
        if not item_ids:
            return []
        rows: list[dict[str, Any]] = []
        for sid in item_ids:
            ref = decode_ref(_lookup_key(int(sid)))
            rows.extend(await self._fetch_one(ref, int(sid), time_from, time_till))
        rows.sort(key=lambda r: (r["item_id"], r["clock"]))
        return rows

    async def _fetch_one(
        self,
        ref: Any,
        synthetic_id: int,
        time_from: int,
        time_till: int,
    ) -> list[dict[str, Any]]:
        """取一个定位串的曲线并分桶。"""
        meta = await self._item_meta(ref.instance_id, ref.item_id)

        params: dict[str, Any] = {
            "instance_id": ref.instance_id,
            "item_id": ref.item_id,
            "start_unix": time_from,
            # 左闭右开：回退 1 秒，避免把次月首日 00:00 的点算进本期
            "end_unix": max(time_from, time_till - 1),
        }

        if ref.is_list_column:
            primary_id = _first_primary(meta)
            if primary_id is None:
                raise AdapterAPIError(
                    None,
                    f"监控项 {ref.item_id} 未解析到主键列，无法定位行 {ref.row_key!r}",
                )
            # data_source_more 与 tags **必须同时给**：只给一个接口不报错，
            # 但返回的是跨行聚合值（实测踩过）
            params["data_source_id"] = ref.column_id
            params["data_source_more"] = ref.row_key
            params["tags"] = json.dumps({str(primary_id): ref.row_key})
        else:
            sources = _first_source(meta)
            if not sources:
                raise AdapterAPIError(
                    None, f"监控项 {ref.item_id} 未解析到数据源列（source 为空）"
                )
            params["data_source_id"] = sources[0]

        data = await self._get("/monitor/instance/item/history", **params)
        nodes = data.get("nodes") or []
        rows = bucket_hourly(nodes, synthetic_id)

        if not rows and ref.is_list_column:
            # 无历史的 number 列：允许 scalar，用当前值兜一个单点。
            # 平台侧 agg 对单点集合的结果就是该值，基线区间无数据时
            # calc_engine 本来就会跳过环比。
            rows = await self._single_point_from_status(ref, synthetic_id)
        return rows

    async def _single_point_from_status(
        self, ref: Any, synthetic_id: int
    ) -> list[dict[str, Any]]:
        rows = await self.list_item_rows(
            instance_id=str(ref.instance_id),
            item_id=str(ref.item_id),
            column_id=str(ref.column_id),
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            if row["row_key"] != ref.row_key:
                continue
            try:
                value = float(row["current_value"])
            except (TypeError, ValueError):
                return []
            ts = int(row.get("as_of") or 0)
            clock = ts - (ts % 3600) if ts else 0
            out.append(
                {
                    "item_id": synthetic_id,
                    "clock": clock,
                    "num": 1,
                    "value_min": value,
                    "value_avg": value,
                    "value_max": value,
                }
            )
        return out


def _first_source(meta: dict[str, Any]) -> list[int]:
    """监控项的第一个数据源列 id（非list 型用）。"""
    raw = meta.get("source")
    if isinstance(raw, list):
        candidates = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        candidates = parsed if isinstance(parsed, list) else []
    else:
        return []
    out: list[int] = []
    for item in candidates:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _first_primary(meta: dict[str, Any]) -> int | None:
    """list 型监控项的主键列 id（拼 tags 用）。"""
    try:
        ids = json.loads(meta.get("module_extension") or "{}").get("primary_ids") or []
        return int(ids[0]) if ids else None
    except (json.JSONDecodeError, TypeError, ValueError, IndexError):
        return None


def _column_of(meta: dict[str, Any], column_id: int | None) -> AIOpsColumn | None:
    """按 column_id 取列元信息；column_id 为 None（非 list 型）返回 None。"""
    if column_id is None:
        return None
    for col in parse_columns(meta):
        if col.column_id == column_id:
            return col
    return None


def _format_scalar(value: Any) -> str:
    """把数值还原成紧凑字符串（text 类型回退路径用）。

    ``%g`` 与平台 ``format_fact_value`` 口径一致：整数不要变成 ``15.0``。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return str(int(number))
    return f"{number:g}"


async def _model_name(adapter: "AIOpsAdapter", model_id: int) -> str:
    """取模型名（带进程内缓存）；缓存空时按需拉一次模型列表。

    ``list_hosts`` 要给每个实例回填所属模型名，但模型名只在 ``model/base/list``
    里 —— 前端通常先加载模型下拉，正常流程下缓存已热；这里兜住「直接打
    hosts 接口」的情况（首屏、深链接、编辑回显）。

    注解刻意写成字符串：函数放在类定义之后，而 **Python 3.11 会在定义处立即
    求值注解**，直接写 ``AIOpsAdapter`` 会 NameError（本机 3.14 延迟求值不报，
    生产 3.11 报 —— 见 AGENTS.md §3.9，已栽过一次）。
    """
    with _CACHE_LOCK:
        hit = _MODEL_NAMES.get(model_id)
    if hit:
        return hit
    await adapter.list_host_groups()
    with _CACHE_LOCK:
        return _MODEL_NAMES.get(model_id, f"模型 #{model_id}")
