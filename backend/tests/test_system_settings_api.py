"""系统设置端点测试：只读、无密钥泄漏、需登录。"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_current_user
from app.database import get_db
from app.main import app
from app.models.base import Base
from app.models.user import User

SETTINGS_KEYS = (
    "smtp_host",
    "smtp_port",
    "smtp_from",
    "smtp_username",
    "smtp_use_tls",
    "smtp_ready",
    "smtp_secret_set",
    "app_base_url",
    "confirm_token_ttl_hours",
    "reminder_interval_hours",
    "task_contact_email",
    "scheduler_enabled",
    "scheduler_running",
    "scheduler_jobs",
    "report_timezone",
    "misfire_grace_hours",
    "onlyoffice_enabled",
    "ai_base_url",
    "ai_ready",
    "ai_secret_set",
    "ai_model",
)


@pytest_asyncio.fixture
async def api(fake_user: User) -> AsyncGenerator[AsyncClient, None]:
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
    app.dependency_overrides[get_current_user] = lambda: fake_user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def anon() -> AsyncGenerator[AsyncClient, None]:
    """未登录客户端。"""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        async with async_sessionmaker(engine, class_=AsyncSession)() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_returns_runtime_overview(api: AsyncClient) -> None:
    r = await api.get("/api/system/settings")
    assert r.status_code == 200, r.text
    body = r.json()
    missing = [k for k in SETTINGS_KEYS if k not in body]
    assert not missing, f"缺少字段 {missing}"
    assert isinstance(body["scheduler_jobs"], list)
    assert isinstance(body["smtp_ready"], bool)


@pytest.mark.asyncio
async def test_never_leaks_secrets(api: AsyncClient) -> None:
    """响应里不得出现任何密钥类字段名。

    注：smtp_username 为非密钥的账号名，为支持页面编辑而返回，不在此列。
    """
    text = (await api.get("/api/system/settings")).text.lower()
    for forbidden in (
        "smtp_password",
        "password",
        "jwt_secret",
        "encryption_key",
        "ai_api_key",
        "onlyoffice_jwt_secret",
    ):
        assert forbidden not in text, f"响应泄漏了 {forbidden}"


@pytest.mark.asyncio
async def test_requires_login(anon: AsyncClient) -> None:
    r = await anon.get("/api/system/settings")
    assert r.status_code == 401