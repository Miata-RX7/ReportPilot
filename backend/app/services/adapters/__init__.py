"""Adapter 工厂：按数据源类型构建实例（注册表即扩展点）。"""

from typing import Any

from app.services.adapters.aiops_adapter import AIOpsAdapter
from app.services.adapters.base import DataSourceAdapter
from app.services.adapters.zabbix_adapter import ZabbixAdapter

_REGISTRY: dict[str, type[DataSourceAdapter]] = {
    "zabbix": ZabbixAdapter,
    "aiops": AIOpsAdapter,
}


def build_adapter(
    ds_type: str,
    config: dict[str, Any],
    secret: str | None = None,
    cache_ns: Any = None,
) -> DataSourceAdapter:
    """构建 Adapter。

    ``cache_ns`` 传数据源 id，用于浏览类缓存的命名空间（见 DataSourceAdapter.__init__）。
    为 None 时 adapter 退化为按实例隔离（测试里直接 new 的情况）。
    """
    cls = _REGISTRY.get(ds_type)
    if cls is None:
        raise ValueError(f"未知数据源类型: {ds_type}")
    return cls(config, secret, cache_ns)


__all__ = ["build_adapter", "DataSourceAdapter"]
