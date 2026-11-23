"""计算型指标可引用**同角色组**成员的指标（2026-11-23，阶段 4）。"""

from collections.abc import AsyncGenerator

import httpx
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
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, session_factory
    app.dependency_overrides.clear()
    await engine.dispose()


def _cfg(metric_id: int) -> dict:
    return {
        "formula": "dep",
        "variables": [{"name": "dep", "metric_id": metric_id}],
    }


class TestPeerReference:
    async def test_peer_in_same_role_is_allowed(self, ctx) -> None:
        """同角色成员：允许引用。"""
        from app.services.calculated_metrics import validate_calculated_config

        _client, sf = ctx
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
                    id=100, owner_id=2, name="对方指标", metric_type="scalar",
                    data_source_id=1, query_config={"item_ids": ["1"]},
                    is_active=True,
                )
            )
            await db.commit()

        async with sf() as db:
            # 不抛错 = 通过
            await validate_calculated_config(
                db,
                query_config=_cfg(100),
                data_source_id=1,
                owner_id=1,
            )

    async def test_non_peer_is_rejected(self, ctx) -> None:
        """非同角色成员：仍然拒绝（错误文案提到角色组）。"""
        from app.services.calculated_metrics import validate_calculated_config

        _client, sf = ctx
        async with sf() as db:
            for uid in (1, 2):
                db.add(
                    User(id=uid, username=f"u{uid}", email=f"u{uid}@t",
                         display_name=f"U{uid}", source="local", role="user",
                         is_active=True)
                )
            db.add(
                MetricDefinition(
                    id=101, owner_id=2, name="外人指标", metric_type="scalar",
                    data_source_id=1, query_config={"item_ids": ["1"]},
                    is_active=True,
                )
            )
            await db.commit()

        async with sf() as db:
            with pytest.raises(ValueError) as exc:
                await validate_calculated_config(
                    db,
                    query_config=_cfg(101),
                    data_source_id=1,
                    owner_id=1,
                )
        assert "角色组" in str(exc.value)
