"""用户管理 API 测试：列表过滤/编辑/停用/删除保护/权限。"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_current_user
from app.database import get_db
from app.main import app
from app.models.base import Base
from app.models.task import ReportTask
from app.models.template import WordTemplate
from app.models.user import User


def _u(uid: int, username: str, role: str = "user", active: bool = True) -> User:
    return User(
        id=uid,
        username=username,
        email=f"{username}@test.local",
        display_name=username.upper(),
        source="local",
        role=role,
        is_active=active,
    )


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as s:
            yield s

    async with factory() as s:
        s.add_all([
            _u(1, "admin1", role="admin"),
            _u(2, "alice"),
            _u(3, "bob", active=False),
        ])
        await s.commit()

    def _client(user: User | None) -> AsyncClient:
        app.dependency_overrides[get_db] = _get_db
        if user is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = lambda: user
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield factory, _client
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_defaults_to_active_only(env):
    _f, make = env
    async with make(_u(1, "admin1", role="admin")) as c:
        r = await c.get("/api/users")
        assert r.status_code == 200
        names = {u["username"] for u in r.json()}
    assert names == {"admin1", "alice"}, "默认不应包含停用用户"


@pytest.mark.asyncio
async def test_list_include_inactive_requires_admin(env):
    _f, make = env
    async with make(_u(2, "alice")) as c:
        assert (await c.get("/api/users", params={"include_inactive": True})).status_code == 403
    async with make(_u(1, "admin1", role="admin")) as c:
        r = await c.get("/api/users", params={"include_inactive": True})
        assert r.status_code == 200
        assert {u["username"] for u in r.json()} == {"admin1", "alice", "bob"}
        assert all("is_active" in u for u in r.json()), "必须返回 is_active 供状态列展示"


@pytest.mark.asyncio
async def test_update_display_name_and_role(env):
    _f, make = env
    async with make(_u(1, "admin1", role="admin")) as c:
        r = await c.patch("/api/users/2", json={"display_name": "爱丽丝", "role": "admin"})
        assert r.status_code == 200, r.text
        assert r.json()["display_name"] == "爱丽丝"
        assert r.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_update_rejects_duplicate_email(env):
    _f, make = env
    async with make(_u(1, "admin1", role="admin")) as c:
        r = await c.patch("/api/users/2", json={"email": "admin1@test.local"})
    assert r.status_code == 409
    assert "已被使用" in r.json()["detail"]


@pytest.mark.asyncio
async def test_cannot_disable_last_admin(env):
    _f, make = env
    async with make(_u(1, "admin1", role="admin")) as c:
        r = await c.patch("/api/users/1", json={"is_active": False})
    assert r.status_code == 409
    assert "管理员" in r.json()["detail"]


@pytest.mark.asyncio
async def test_non_admin_cannot_modify(env):
    _f, make = env
    async with make(_u(2, "alice")) as c:
        assert (await c.patch("/api/users/3", json={"is_active": True})).status_code == 403
        assert (await c.delete("/api/users/3")).status_code == 403


@pytest.mark.asyncio
async def test_toggle_active(env):
    _f, make = env
    async with make(_u(1, "admin1", role="admin")) as c:
        assert (await c.patch("/api/users/2", json={"is_active": False})).status_code == 200
        assert (await c.get("/api/users")).json() is not None
        r = await c.get("/api/users")
        assert "alice" not in {u["username"] for u in r.json()}, "停用后默认列表不再出现"
        assert (await c.patch("/api/users/2", json={"is_active": True})).status_code == 200


@pytest.mark.asyncio
async def test_delete_blocked_by_reference(env):
    """被任务/模板引用时拒绝删除，提示改用停用。"""
    factory, make = env
    async with factory() as s:
        tpl = WordTemplate(name="t", owner_id=2, placeholders_json=[], current_version_id=None)
        s.add(tpl)
        await s.flush()
        s.add(
            ReportTask(
                name="t1", template_id=tpl.id, cron_expr="0 9 1 * *",
                confirmer_email="alice@test.local",
                recipient_emails=["x@y.com"], is_active=False, created_by=2,
            )
        )
        await s.commit()
    async with make(_u(1, "admin1", role="admin")) as c:
        r = await c.delete("/api/users/2")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "无法删除" in detail and "停用" in detail


@pytest.mark.asyncio
async def test_delete_unreferenced_user(env):
    _f, make = env
    async with make(_u(1, "admin1", role="admin")) as c:
        assert (await c.delete("/api/users/3")).status_code == 204
        r = await c.get("/api/users", params={"include_inactive": True})
        assert "bob" not in {u["username"] for u in r.json()}


@pytest.mark.asyncio
async def test_cannot_delete_self(env):
    _f, make = env
    async with make(_u(1, "admin1", role="admin")) as c:
        r = await c.delete("/api/users/1")
    assert r.status_code == 409
    assert "自己" in r.json()["detail"]


@pytest.mark.asyncio
async def test_requires_login(env):
    _f, make = env
    async with make(None) as c:
        assert (await c.get("/api/users")).status_code == 401

def _payload(**kw):
    base = {
        "username": "newuser",
        "display_name": "新用户",
        "email": "new@test.local",
        "password": "pass12345",
        "role": "user",
        "is_active": True,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_create_user(env):
    from app.models.user import User
    from app.services.security import verify_password

    factory, make = env
    async with make(_u(1, "admin1", role="admin")) as c:
        r = await c.post("/api/users", json=_payload())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["username"] == "newuser"
    assert body["role"] == "user"
    assert body["is_active"] is True
    assert "password" not in body and "password_hash" not in body, "不得返回密码字段"

    async with factory() as s:
        u = await s.get(User, body["id"])
        assert u is not None
        assert u.password_hash and u.password_hash != "pass12345"
        assert verify_password("pass12345", u.password_hash), "密码应被正确哈希"


@pytest.mark.asyncio
async def test_create_user_duplicate_username_or_email(env):
    _f, make = env
    async with make(_u(1, "admin1", role="admin")) as c:
        r1 = await c.post("/api/users", json=_payload(username="alice"))
        assert r1.status_code == 409
        assert "登录名已存在" in r1.json()["detail"]
        r2 = await c.post("/api/users", json=_payload(email="alice@test.local"))
        assert r2.status_code == 409
        assert "邮箱已被使用" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_create_user_validates_password_length(env):
    _f, make = env
    async with make(_u(1, "admin1", role="admin")) as c:
        assert (await c.post("/api/users", json=_payload(password="short"))).status_code == 422
        assert (await c.post("/api/users", json=_payload(username="x"))).status_code == 422


@pytest.mark.asyncio
async def test_create_user_requires_admin(env):
    _f, make = env
    async with make(_u(2, "alice")) as c:
        assert (await c.post("/api/users", json=_payload())).status_code == 403
    async with make(None) as c:
        assert (await c.post("/api/users", json=_payload())).status_code == 401


@pytest.mark.asyncio
async def test_new_user_can_login(env):
    """新建后可用初始密码登录（端到端打通新建 -> 登录）。"""
    factory, make = env
    async with make(_u(1, "admin1", role="admin")) as c:
        await c.post("/api/users", json=_payload(username="li.ming", email="li.ming@test.local"))
    async with make(None) as c:
        r = await c.post(
            "/api/auth/login", json={"username": "li.ming", "password": "pass12345"}
        )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["username"] == "li.ming"
