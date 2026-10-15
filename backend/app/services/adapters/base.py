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
    async def fetch_latest_value(self, item_id: int) -> dict[str, Any] | None:
        """取单个监控项的**最新一个原始采样值**（history.get limit=1）。

        供 text 类型 mode=latest 使用：需要原始值而非小时聚合，不能复用
        fetch_trend_data。无数据时返回 None。
        """

    @abstractmethod
    async def fetch_latest_value(self, item_id: int) -> dict[str, Any] | None:
        """取单个监控项的**最新一条原始采样值**（history.get limit=1）。

        与 fetch_trend_data 的区别：返回原始值（不做小时聚合），且不做任何聚合。
        text 类型的 mode=latest 与 pie 适合性判定共用本方法。
        返回 None 表示该 item 无历史数据。
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
