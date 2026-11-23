"""指标列表的「范围」筛选（scope=all/mine/shared，2026-11-23）。"""

from collections.abc import AsyncGenerator

import httpx
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
from app.models.role import Role, RoleMember
from app.models.user import User


@pytest_asyncio.fixture
async def ctx() -> AsyncGenerator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession]], None
]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    user = User(
        id=1, username="u1", email="u1@t", display_name="U1",
        source="local", role="user", is_active=True,
    )
    app.dependency_overrides[get_db] = _get_db

    async def _current():
        # 真实请求路径里 get_current_user 会挂载共享范围；这里覆盖了它，
        # 所以要自己挂一次（否则判定保守 -> 只看自己的）。
        from app.services.ownership import attach_share_scope

        async with session_factory() as db:
            await attach_share_scope(db, user)
        return user

    app.dependency_overrides[get_current_user] = _current
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, session_factory
    app.dependency_overrides.clear()
    await engine.dispose()


class TestScopeFilter:
    async def test_scope_mine_shared_all(self, ctx) -> None:
        """scope=mine 只回自己的；scope=shared 只回角色组共享的。"""
        client, sf = ctx
        async with sf() as db:
            for uid in (1, 2):
                db.add(
                    User(id=uid, username=f"u{uid}", email=f"u{uid}@t",
                         display_name=f"U{uid}", source="local", role="user",
                         is_active=True)
                )
            db.add(Role(id=10, name="组", description="", created_by=1))
            db.add(RoleMember(role_id=10, user_id=1, added_by=1))
            db.add(RoleMember(role_id=10, user_id=2, added_by=1))
            db.add(
                MetricDefinition(
                    id=101, owner_id=1, name="我的指标", metric_type="scalar",
                    data_source_id=1, query_config={}, is_active=True,
                )
            )
            db.add(
                MetricDefinition(
                    id=102, owner_id=2, name="共享指标", metric_type="scalar",
                    data_source_id=1, query_config={}, is_active=True,
                )
            )
            await db.commit()

        r = await client.get("/api/metrics/paged?scope=all&status=all")
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 2, r.json()

        r = await client.get("/api/metrics/paged?scope=mine&status=all")
        assert r.json()["total"] == 1, r.json()
        assert r.json()["items"][0]["name"] == "我的指标"

        r = await client.get("/api/metrics/paged?scope=shared&status=all")
        assert r.json()["total"] == 1, r.json()
        assert r.json()["items"][0]["name"] == "共享指标"

    async def test_invalid_scope_rejected(self, ctx) -> None:
        """非法 scope -> 422。"""
        client, _sf = ctx
        r = await client.get("/api/metrics/paged?scope=bogus")
        assert r.status_code == 422, r.text
