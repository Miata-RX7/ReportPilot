"""auth 单元与集成测试（真实 JWT，不 override get_current_user）。"""

import logging
from collections.abc import AsyncGenerator

import jwt as pyjwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.api.auth as auth_module
from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models import Base
from app.models.user import User
from app.services.security import (
    TokenError,
    TokenExpiredError,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)

PASSWORD = "secret123"  # noqa: S105（测试专用）


@pytest.fixture(autouse=True)
def no_login_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module, "LOGIN_DELAY_SECONDS", 0)


@pytest_asyncio.fixture
async def api() -> AsyncGenerator[tuple[AsyncClient, async_sessionmaker[AsyncSession]], None]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, session_factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed_user(
    session_factory: async_sessionmaker[AsyncSession],
    username: str = "ops",
    role: str = "admin",
    is_active: bool = True,
) -> int:
    async with session_factory() as session:
        user = User(
            username=username,
            email=f"{username}@test.local",
            display_name=username,
            source="local",
            role=role,
            is_active=is_active,
            password_hash=hash_password(PASSWORD),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


class TestSecurityFunctions:
    def test_hash_verify_roundtrip(self) -> None:
        hashed = hash_password(PASSWORD)
        assert verify_password(PASSWORD, hashed) is True
        assert verify_password("wrong-password", hashed) is False

    def test_short_password_rejected(self) -> None:
        with pytest.raises(ValueError, match="至少 8 位"):
            hash_password("short")

    def test_corrupted_hash_returns_false(self) -> None:
        assert verify_password(PASSWORD, "not-a-hash") is False

    def test_token_roundtrip(self) -> None:
        token = create_token(1, "ops", "admin")
        payload = decode_token(token)
        assert payload["sub"] == "1"
        assert payload["username"] == "ops"
        assert payload["role"] == "admin"

    def test_expired_token(self) -> None:
        token = create_token(1, "ops", "admin", expire_days=-1)
        with pytest.raises(TokenExpiredError):
            decode_token(token)

    def test_tampered_token(self) -> None:
        token = create_token(1, "ops", "admin")
        with pytest.raises(TokenError):
            decode_token(token + "tampered")

    def test_non_numeric_sub_rejected(self) -> None:
        forged = pyjwt.encode(
            {"sub": "abc", "role": "admin"},
            get_settings().jwt_secret,
            algorithm="HS256",
        )
        with pytest.raises(TokenError):
            decode_token(forged)


class TestLogin:
    async def test_login_success(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, session_factory = api
        await _seed_user(session_factory)
        resp = await client.post(
            "/api/auth/login", json={"username": "ops", "password": PASSWORD}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert body["user"]["role"] == "admin"
        payload = decode_token(body["access_token"])
        assert payload["username"] == "ops"

    async def test_login_wrong_password(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, session_factory = api
        await _seed_user(session_factory)
        resp = await client.post(
            "/api/auth/login", json={"username": "ops", "password": "wrong-password"}
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "用户名或密码错误"

    async def test_login_unknown_user_same_message(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        resp = await client.post(
            "/api/auth/login", json={"username": "ghost", "password": PASSWORD}
        )
        assert resp.status_code == 401
        # 响应与用户不存在/密码错误保持一致，防枚举
        assert resp.json()["detail"] == "用户名或密码错误"

    async def test_login_inactive_user(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, session_factory = api
        await _seed_user(session_factory, is_active=False)
        resp = await client.post(
            "/api/auth/login", json={"username": "ops", "password": PASSWORD}
        )
        assert resp.status_code == 401

    async def test_password_not_in_logs(
        self,
        api: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        client, session_factory = api
        await _seed_user(session_factory)
        with caplog.at_level(logging.DEBUG):
            await client.post(
                "/api/auth/login", json={"username": "ops", "password": PASSWORD}
            )
            await client.post(
                "/api/auth/login", json={"username": "ops", "password": "wrong-password"}
            )
        assert PASSWORD not in caplog.text
        assert "wrong-password" not in caplog.text
        # 日志不区分用户不存在/密码错误
        assert "用户不存在" not in caplog.text and "密码错误" not in caplog.text


class TestMe:
    async def test_me_with_token(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, session_factory = api
        uid = await _seed_user(session_factory)
        token = create_token(uid, "ops", "admin")
        resp = await client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["username"] == "ops"

    async def test_me_no_token(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        assert (await client.get("/api/auth/me")).status_code == 401

    async def test_me_expired_token(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, session_factory = api
        uid = await _seed_user(session_factory)
        token = create_token(uid, "ops", "admin", expire_days=-1)
        resp = await client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 401
        assert "过期" in resp.json()["detail"]

    async def test_me_deactivated_user(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, session_factory = api
        uid = await _seed_user(session_factory)
        token = create_token(uid, "ops", "admin")
        async with session_factory() as session:
            user = await session.get(User, uid)
            user.is_active = False  # type: ignore[union-attr]
            await session.commit()
        resp = await client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 401


class TestAccessControl:
    """401/403 边界：无 token -> 401；有效 token 非 admin -> 403。"""

    async def _login(self, client: AsyncClient) -> str:
        resp = await client.post(
            "/api/auth/login", json={"username": "ops", "password": PASSWORD}
        )
        return resp.json()["access_token"]

    async def test_no_token_write_401(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, _ = api
        resp = await client.post("/api/datasources", json={})
        assert resp.status_code == 401

    async def test_non_admin_write_403(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, session_factory = api
        await _seed_user(session_factory, role="user")
        token = await self._login(client)
        resp = await client.post(
            "/api/datasources",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    async def test_non_admin_read_allowed(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        client, session_factory = api
        await _seed_user(session_factory, role="user")
        token = await self._login(client)
        resp = await client.get(
            "/api/datasources", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200

    async def test_reports_requires_login_regression(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """回归：不带任何 header 调 POST /api/reports -> 401。

        防止端点漏加 get_current_user 依赖（写操作与读操作均要求登录）。
        """
        client, _ = api
        resp = await client.post("/api/reports", json={})
        assert resp.status_code == 401
        assert (await client.get("/api/reports")).status_code == 401
