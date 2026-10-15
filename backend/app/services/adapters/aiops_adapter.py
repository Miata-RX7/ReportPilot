"""AIOps 数据源 Adapter。

Phase 2+ 才实现，此处仅保留空类作为架构扩展点（设计原则 4）。
"""

from typing import Any

from app.services.adapters.base import DataSourceAdapter

_MSG = "AIOps 数据源将在 Phase 2 实现"


class AIOpsAdapter(DataSourceAdapter):
    """AIOps 平台接入占位，暂不实现。"""

    async def test_connection(self) -> bool:
        raise NotImplementedError(_MSG)

    async def list_host_groups(self) -> list[dict[str, Any]]:
        raise NotImplementedError(_MSG)

    async def list_hosts(self, group_ids: list[str]) -> list[dict[str, Any]]:
        raise NotImplementedError(_MSG)

    async def list_items(
        self,
        host_ids: list[str],
        value_types: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(_MSG)

    async def fetch_trend_data(
        self,
        item_ids: list[int],
        time_from: int,
        time_till: int,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(_MSG)
