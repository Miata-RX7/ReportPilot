"""批量设置指标标签（2026-11-25）。

需求：多选指标 -> 批量增加标签（覆盖）；另有「置空」-> 清空标签。
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
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
from app.models.metric import MetricDefinition
from app.models.user import User


@pytest_asyncio.fixture
async def ctx() -> AsyncGenerator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession], dict], None
]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sf() as session:
            yield session

    users = {
        1: User(id=1, username="u1", email="u1@t", display_name="U1",
                 source="local", role="user", is_active=True),
        2: User(id=2, username="u2", email="u2@t", display_name="U2",
                 source="local", role="user", is_active=True),
    }

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: users[1]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, sf, users
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed(sf):
    async with sf() as db:
        db.add_all([
            User(id=1, username="u1", email="u1@t", display_name="U1",
                 source="local", role="user", is_active=True),
            User(id=2, username="u2", email="u2@t", display_name="U2",
                 source="local", role="user", is_active=True),
            MetricDefinition(id=11, owner_id=1, name="m1", metric_type="scalar",
                             data_source_id=1, query_config={}, tags=["旧标签"]),
            MetricDefinition(id=12, owner_id=1, name="m2", metric_type="scalar",
                             data_source_id=1, query_config={}, tags=[]),
            MetricDefinition(id=13, owner_id=2, name="别人的", metric_type="scalar",
                             data_source_id=1, query_config={}, tags=["x"]),
        ])
        await db.commit()


class TestBatchTags:
    async def test_overwrite_tags(self, ctx) -> None:
        """给标签 -> **覆盖**原有的。"""
        client, sf, _ = ctx
        await _seed(sf)
        r = await client.post(
            "/api/metrics/batch/tags",
            json={"metric_ids": [11, 12], "tags": ["新标签", "另一个"]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["updated"] == 2
        async with sf() as db:
            m = await db.get(MetricDefinition, 11)
            assert m.tags == ["新标签", "另一个"], m.tags

    async def test_clear_tags(self, ctx) -> None:
        """tags 传 null -> 清空（不论原来有没有标签）。"""
        client, sf, _ = ctx
        await _seed(sf)
        r = await client.post(
            "/api/metrics/batch/tags",
            json={"metric_ids": [11, 12], "tags": None},
        )
        assert r.status_code == 200, r.text
        async with sf() as db:
            for mid in (11, 12):
                m = await db.get(MetricDefinition, mid)
                assert m.tags == [], (mid, m.tags)

    async def test_dedupe_and_trim(self, ctx) -> None:
        """去重、去空白。"""
        client, sf, _ = ctx
        await _seed(sf)
        r = await client.post(
            "/api/metrics/batch/tags",
            json={"metric_ids": [11], "tags": [" a ", "a", "b", ""]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["tags"] == ["a", "b"]

    async def test_reject_other_owner(self, ctx) -> None:
        """别人的指标 -> 404（不可见，不泄露 id 存在；整批拒绝，不改一半）。"""
        client, sf, _ = ctx
        await _seed(sf)
        r = await client.post(
            "/api/metrics/batch/tags",
            json={"metric_ids": [11, 13], "tags": ["x"]},
        )
        # 普通用户看不到别人的指标 -> 404（不泄露「这个 id 存在」）
        assert r.status_code == 404, r.text
        async with sf() as db:
            m = await db.get(MetricDefinition, 11)
            assert m.tags == ["旧标签"], m.tags

    async def test_missing_metric_404(self, ctx) -> None:
        """不存在的 id -> 404。"""
        client, sf, _ = ctx
        await _seed(sf)
        r = await client.post(
            "/api/metrics/batch/tags",
            json={"metric_ids": [999], "tags": ["x"]},
        )
        assert r.status_code == 404, r.text
