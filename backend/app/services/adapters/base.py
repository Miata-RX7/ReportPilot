"""数据源 Adapter 抽象基类与统一异常。

所有数据源（Zabbix / AIOps / ...）必须实现该接口，
新增数据源 = 新增 Adapter 实现 + DataSource 配置记录，不改流水线代码。

异常分层（test_connection 通过异常类型区分失败原因）：
- AdapterConnectionError: URL 不可达 / 超时 / 服务端 5xx
- AdapterAuthError:       token 无效或已过期
- AdapterPermissionError: 认证通过但权限不足
- AdapterAPIError:        其他协议层错误（携带业务错误码）
"""

from abc import ABC, abstractmethod
from typing import Any


class AdapterError(Exception):
    """Adapter 层异常基类。消息中禁止包含 token / Authorization 头。"""


class AdapterConnectionError(AdapterError):
    """URL 不可达、超时或服务端错误。"""


class AdapterAuthError(AdapterError):
    """认证失败（token 无效 / 过期）。"""


class AdapterPermissionError(AdapterError):
    """认证通过但权限不足。"""


class AdapterAPIError(AdapterError):
    """协议层业务错误。"""

    def __init__(self, code: int | None, message: str) -> None:
        self.code = code
        super().__init__(f"RPC {code}: {message}" if code is not None else message)


class DataSourceAdapter(ABC):
    """数据源接入统一接口。

    secret 由调用方（API/service 层）从 secret_encrypted 解密后传入，
    仅存活于内存；实现类禁止将其写入日志、异常消息或返回值。
    """

    #: 取数范围的语义标签。``host`` = 上级是「主机组」、下级是「主机」（Zabbix）；
    #: ``model_instance`` = 上级是「模型」、下级是「实例」（AIOps）。
    #: calc_engine 据此选择读query_config 的哪一组字段。
    scope_kind: str = "host"

    def __init__(self, config: dict[str, Any], secret: str | None = None) -> None:
        self.config = config
        self._secret = secret

    @abstractmethod
    async def test_connection(self) -> bool:
        """验证连接配置与凭据可用性。失败时抛出上述分类异常。"""

    @abstractmethod
    async def list_host_groups(self) -> list[dict[str, Any]]:
        """列出主机组，供指标配置选择。"""

    @abstractmethod
    async def list_hosts(self, group_ids: list[str]) -> list[dict[str, Any]]:
        """按主机组列出主机（group_ids 必传，不支持空=全量）。

        返回 [{"host_id", "name", "groups": [{"group_id", "name"}]}]。
        """

    @abstractmethod
    async def list_items(
        self,
        host_ids: list[str],
        value_types: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """按主机列出监控项（host_ids 必传）。value_types 为空时由实现类给默认过滤。"""

    @abstractmethod
    async def fetch_latest_value(
        self,
        item_id: int,
        time_from: int | None = None,
        time_till: int | None = None,
    ) -> dict[str, Any] | None:
        """取单个监控项的**最新一条原始采样值**（Zabbix: history.get limit=1）。

        与 fetch_trend_data 的区别：返回原始值（不做小时聚合），且不做任何聚合。
        text 类型的 mode=latest 与 pie 适合性判定共用本方法。
        返回 None 表示该 item 无历史数据。

        ``time_from`` / ``time_till`` 是可选区间（Unix 秒）。Zabbix 侧忽略
        （取服务端保留的最后一条，行为与升级前完全一致）；AIOps 侧用来实现
        「取周期末点」而不是「取调用时刻的当前值」—— 实测 AIOps 的 text 列
        同样有历史，所以月报的 text 指标应取周期内最后一个点，重跑结果才稳定。
        """

    @abstractmethod
    async def get_item_meta(self, item_id: int) -> dict[str, Any] | None:
        """取单个监控项的元信息（value_type / valuemap），供 pie 适合性判定。

        返回 {"item_id", "name", "key", "value_type", "units", "valuemap"}；
        不存在时返回 None。list_items 不返回 valuemap，故单列一个方法。
        """

    @abstractmethod
    async def fetch_trend_data(
        self,
        item_ids: list[int],
        time_from: int,
        time_till: int,
    ) -> list[dict[str, Any]]:
        """拉取 [time_from, time_till]（Unix 秒）内的趋势数据。

        趋势数据通常按小时聚合。实现类应透传每小时的 min/avg/max 三列，
        由 calc_engine 按 calc_formula.agg 做二次聚合（本期/基线同口径）。
        """

    async def normalize_item_keys(self, keys: list[str]) -> dict[str, int]:
        """把 query_config 里存的**稳定定位串**映射为进程内合成整数 id。

        为什么需要这一层：calc_engine 内部对 item_id 做``int()`` 转换并用
        ``dict[int, ...]`` 分组（见 resolve_series_items / _compute_curve），
        所以 item_id 必须是整数。而 AIOps 的一个「可取数对象」是四元组
        ``(实例, 监控项, 列, 主键行)``，其中主键行是字符串（如
        ``GigabitEthernet1/0/1``），无法编码进整数。

        实现约定：query_config 里存稳定可读的定位串，计算开始时经本方法换成
        合成整数，后续 fetch_trend_data 再反查回定位串。默认实现（Zabbix）
        直接把数字串转 int，**存量配置零改动**。

        必须是「稳定」的：同一份 query_config 在不同进程/不同时间解析必须得到
        同样的定位串，否则反查会落空。
        """
        out: dict[str, int] = {}
        for key in keys:
            try:
                out[key] = int(key)
            except (TypeError, ValueError):
                raise AdapterAPIError(
                    None, f"取数对象标识 {str(key)[:48]!r} 不是合法 id"
                ) from None
        return out

    async def list_item_rows(
        self,
        *,
        instance_id: str,
        item_id: str,
        column_id: str,
    ) -> list[dict[str, Any]]:
        """列出 list 型监控项的主键行（仅 AIOps 支持）。

        返回 ``[{"row_key": str}]``，按主键列顺序返回。默认实现直接报错，
        Zabbix 没有 list 型监控项，走不到这里。
        """
        raise AdapterAPIError(None, "该数据源不支持 list 型监控项的主键行")

    async def resolve_scoped_items(
        self,
        *,
        upper_ids: list[str],
        lower_ids: list[str],
        keys: list[str],
    ) -> list[dict[str, Any]]:
        """把 query_config 的定位串解析成带**合成整数 id** 的监控项元信息。

        这一步把三件事收在一起，所以数据源之间的差异不会渗进 calc_engine：
        1. 下级必须属于所选上级（防绕过前端直接构造 query_config）
        2. 定位串必须能解析，且其指向的监控项确实存在于这些下级上
        3. 返回的每个 dict 带 ``item_id``（**整数**，已过 normalize_item_keys），
           以及 name / host_id / host 供object、formula 生成人类可读证据

        默认实现是 Zabbix 语义（定位串即数字 item_id），存量配置零改动。
        失败抛 ``AdapterAPIError``；调用方会转成 ``MetricComputeError`` 以保持
        422 语义（AdapterError 默认会被映射成 502「数据源侧问题」）。
        """
        hosts = await self.list_hosts(upper_ids)
        valid = {str(h["host_id"]) for h in hosts}
        unknown = sorted({str(x) for x in lower_ids} - valid)
        if unknown:
            raise AdapterAPIError(
                None, f"以下主机不属于所选主机组 {upper_ids}: {unknown}"
            )
        candidates = await self.list_items([str(x) for x in lower_ids])
        mapping = await self.normalize_item_keys(keys)
        found = {str(c["item_id"]) for c in candidates}
        missing = sorted(k for k in keys if k not in found)
        if missing:
            raise AdapterAPIError(None, f"以下监控项未在指定主机中找到: {missing}")
        by_id = {str(c["item_id"]): c for c in candidates}
        return [
            {
                "item_id": mapping[key],
                "name": str(by_id[key].get("name") or ""),
                "key": str(by_id[key].get("key") or ""),
                "host_id": str(by_id[key].get("host_id") or ""),
                "host": str(by_id[key].get("host") or ""),
                "units": str(by_id[key].get("units") or ""),
            }
            for key in keys
        ]
