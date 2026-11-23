"""角色组（共享圈）核心回归测试（2026-11-23）。"""

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
from app.models.task import ReportTask
from app.models.user import User


def _user(uid: int, role: str = "user") -> User:
    return User(
        id=uid,
        username=f"u{uid}",
        email=f"u{uid}@test.local",
        display_name=f"用户{uid}",
        source="local",
        role=role,
        is_active=True,
    )


@pytest_asyncio.fixture
async def ctx() -> AsyncGenerator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession], dict[int, User]], None
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

    users: dict[int, User] = {}

    def _current() -> User:
        # 由测试切换（_current.which）
        uid = _current.which
        if uid not in users:
            users[uid] = _user(uid)
        return users[uid]

    _current.which = 1
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = _current
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, session_factory, users
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed(session_factory, *, role_ids: list[int], members: dict[int, list[int]]):
    async with session_factory() as db:
        for rid in role_ids:
            db.add(Role(id=rid, name=f"组{rid}", description="", created_by=1))
        for rid, uids in members.items():
            for uid in uids:
                db.add(RoleMember(role_id=rid, user_id=uid, added_by=1))
        await db.commit()


class TestShareScope:
    async def test_scope_includes_same_role_members(self, ctx) -> None:
        """同角色成员进入共享范围。"""
        from app.services.ownership import attach_share_scope

        client, sf, _users = ctx
        async with sf() as db:
            db.add_all(
                [
                    User(id=1, username="a", email="a@t", display_name="A", source="local", role="user", is_active=True),
                    User(id=2, username="b", email="b@t", display_name="B", source="local", role="user", is_active=True),
                ]
            )
            await db.commit()
        await _seed(sf, role_ids=[10], members={10: [1, 2]})

        async with sf() as db:
            u1 = await db.get(User, 1)
            scope = await attach_share_scope(db, u1)
            assert scope == frozenset({1, 2}), scope

    async def test_scope_multiple_roles(self, ctx) -> None:
        """一人可属多个角色 -> 范围取并集。"""
        from app.services.ownership import attach_share_scope

        client, sf, _users = ctx
        async with sf() as db:
            for uid in (1, 2, 3):
                db.add(
                    User(id=uid, username=f"u{uid}", email=f"u{uid}@t", display_name=f"U{uid}", source="local", role="user", is_active=True)
                )
            await db.commit()
        await _seed(sf, role_ids=[10, 20], members={10: [1, 2], 20: [1, 3]})

        async with sf() as db:
            u1 = await db.get(User, 1)
            scope = await attach_share_scope(db, u1)
            assert scope == frozenset({1, 2, 3}), scope

    async def test_admin_scope_is_empty(self, ctx) -> None:
        """admin 可见全部，不需要共享范围。"""
        from app.services.ownership import attach_share_scope

        client, sf, _users = ctx
        async with sf() as db:
            db.add(
                User(id=1, username="root", email="r@t", display_name="R", source="local", role="admin", is_active=True)
            )
            await db.commit()
        async with sf() as db:
            u = await db.get(User, 1)
            assert await attach_share_scope(db, u) == frozenset()


class TestVisibility:
    def test_peer_visible_but_not_editable(self) -> None:
        """同角色成员**可见但不可编辑**。"""
        from app.services import ownership

        class U:
            id = 1
            role = "user"

        class M:
            owner_id = 2

        u = U()
        setattr(u, "_share_scope", frozenset({1, 2}))
        assert ownership.metric_visible_to(M(), u)
        assert not ownership.metric_editable_by(M(), u)

    def test_task_visible_but_not_editable(self) -> None:
        """任务：**可见 ≠ 可编辑**（原先 task_editable_by == visible）。"""
        from app.services import ownership

        class U:
            id = 1
            role = "user"

        class T:
            created_by = 2

        u = U()
        setattr(u, "_share_scope", frozenset({1, 2}))
        assert ownership.task_visible_to(T(), u)
        assert not ownership.task_editable_by(T(), u)

    def test_no_scope_means_own_only(self) -> None:
        """未挂载共享范围时**保守**：只看自己的（不会误放权）。"""
        from app.services import ownership

        class U:
            id = 1
            role = "user"

        class M:
            owner_id = 2

        u = U()
        assert not ownership.metric_visible_to(M(), u)


class TestRolesApi:
    async def test_admin_can_manage_roles(self, ctx) -> None:
        """admin 能建角色、加成员、列成员、删成员、删角色。"""
        client, sf, users = ctx
        # 当前用户 = admin（require_admin 走 get_current_user 的覆盖）
        users[1] = _user(1, "admin")
        async with sf() as db:
            db.add(
                User(id=1, username="root", email="r@t", display_name="R", source="local", role="admin", is_active=True)
            )
            db.add(
                User(id=2, username="u2", email="u2@t", display_name="U2", source="local", role="user", is_active=True)
            )
            await db.commit()

        r = await client.post("/api/roles", json={"name": "海油专线组", "description": "d"})
        assert r.status_code == 201, r.text
        rid = r.json()["id"]

        r = await client.post(f"/api/roles/{rid}/members", json={"user_ids": [2]})
        assert r.status_code == 200, r.text
        assert [m["user_id"] for m in r.json()] == [2]

        r = await client.get(f"/api/roles/{rid}/members")
        assert r.status_code == 200
        assert r.json()[0]["username"] == "u2"

        r = await client.delete(f"/api/roles/{rid}/members/2")
        assert r.status_code == 204, r.text
        r = await client.get(f"/api/roles/{rid}/members")
        assert r.json() == []

        r = await client.delete(f"/api/roles/{rid}")
        assert r.status_code == 204, r.text

    async def test_normal_user_only_sees_own_roles(self, ctx) -> None:
        """普通用户**只看到自己加入的**角色。"""
        client, sf, _users = ctx
        async with sf() as db:
            db.add(
                User(id=1, username="root", email="r@t", display_name="R", source="local", role="admin", is_active=True)
            )
            for uid in (2, 3):
                db.add(
                    User(id=uid, username=f"u{uid}", email=f"u{uid}@t", display_name=f"U{uid}", source="local", role="user", is_active=True)
                )
            await db.commit()
        await _seed(sf, role_ids=[10, 20], members={10: [2], 20: [3]})

        # 切到用户 2
        import app.api.deps as deps_mod

        users_map = {2: _user(2), 3: _user(3)}
        app.dependency_overrides[get_current_user] = lambda: users_map[2]
        r = await client.get("/api/roles")
        assert r.status_code == 200, r.text
        names = [x["name"] for x in r.json()]
        assert names == ["组10"], names

        # 看不到别人的角色成员
        r = await client.get("/api/roles/20/members")
        assert r.status_code == 404, r.text

    async def test_normal_user_cannot_create_role(self, ctx) -> None:
        """普通用户不能建角色。"""
        client, sf, _users = ctx
        async with sf() as db:
            db.add(
                User(id=2, username="u2", email="u2@t", display_name="U2", source="local", role="user", is_active=True)
            )
            await db.commit()
        app.dependency_overrides[get_current_user] = lambda: _user(2)
        r = await client.post("/api/roles", json={"name": "x"})
        assert r.status_code == 403, r.text

    async def test_duplicate_name_rejected(self, ctx) -> None:
        """角色名全局唯一。"""
        client, sf, users = ctx
        users[1] = _user(1, "admin")
        async with sf() as db:
            db.add(
                User(id=1, username="root", email="r@t", display_name="R", source="local", role="admin", is_active=True)
            )
            await db.commit()
        r1 = await client.post("/api/roles", json={"name": "同名组"})
        assert r1.status_code == 201
        r2 = await client.post("/api/roles", json={"name": "同名组"})
        assert r2.status_code == 409, r2.text
