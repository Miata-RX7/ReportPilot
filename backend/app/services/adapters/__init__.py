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
) -> DataSourceAdapter:
    cls = _REGISTRY.get(ds_type)
    if cls is None:
        raise ValueError(f"未知数据源类型: {ds_type}")
    return cls(config, secret)


__all__ = ["build_adapter", "DataSourceAdapter"]
