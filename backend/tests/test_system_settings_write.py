"""系统设置写接口测试：管理员鉴权、密钥不泄漏、"留空即不修改"语义。"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_current_user
from app.database import get_db
from app.main import app
from app.models.app_setting import AppSetting
from app.models.base import Base
from app.models.user import User
from app.services.runtime_config import effective_settings

SessionFactory = async_sessionmaker[AsyncSession]

MAIL_PAYLOAD = {
    "smtp_host": "smtp.example.com",
    "smtp_port": 465,
    "smtp_username": "bot@example.com",
    "smtp_from": "bot@example.com",
    "smtp_use_tls": True,
    "app_base_url": "https://reports.example.com",
    "smtp_password": "s3cret-pass",
}


async def _make_api(
    user: User | None,
) -> AsyncGenerator[tuple[AsyncClient, SessionFactory], None]:
    """user=None 表示不注入登录态（用于 401 用例）。"""
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
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, session_factory
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest_asyncio.fixture
async def admin_api(
    fake_user: User,
) -> AsyncGenerator[tuple[AsyncClient, SessionFactory], None]:
    async for pair in _make_api(fake_user):
        yield pair


@pytest_asyncio.fixture
async def viewer_api() -> AsyncGenerator[tuple[AsyncClient, SessionFactory], None]:
    viewer = User(
        id=2,
        username="viewer",
        email="viewer@test.local",
        display_name="Viewer",
        source="local",
        role="user",
        is_active=True,
    )
    async for pair in _make_api(viewer):
        yield pair


@pytest_asyncio.fixture
async def anon_api() -> AsyncGenerator[tuple[AsyncClient, SessionFactory], None]:
    async for pair in _make_api(None):
        yield pair


@pytest.mark.asyncio
async def test_mail_update_requires_admin(
    viewer_api: tuple[AsyncClient, SessionFactory],
) -> None:
    client, _ = viewer_api
    r = await client.put("/api/system/settings/mail", json=MAIL_PAYLOAD)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_write_requires_login(
    anon_api: tuple[AsyncClient, SessionFactory],
) -> None:
    client, _ = anon_api
    assert (await client.put("/api/system/settings/mail", json=MAIL_PAYLOAD)).status_code == 401
    assert (await client.put("/api/system/settings/ai", json={})).status_code == 401


@pytest.mark.asyncio
async def test_mail_update_persists_and_hides_password(
    admin_api: tuple[AsyncClient, SessionFactory],
) -> None:
    client, session_factory = admin_api
    r = await client.put("/api/system/settings/mail", json=MAIL_PAYLOAD)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["smtp_host"] == "smtp.example.com"
    assert body["smtp_port"] == 465
    assert body["smtp_username"] == "bot@example.com"
    assert body["smtp_ready"] is True
    assert body["smtp_secret_set"] is True
    assert MAIL_PAYLOAD["smtp_password"] not in r.text

    # 只读概览同样不回显密码，且读到保存后的值
    got = await client.get("/api/system/settings")
    assert got.status_code == 200
    assert got.json()["smtp_host"] == "smtp.example.com"
    assert MAIL_PAYLOAD["smtp_password"] not in got.text

    # 发信 / 确认链接等运行路径读取的是覆盖后的配置
    assert effective_settings().smtp_host == "smtp.example.com"
    assert effective_settings().app_base_url == "https://reports.example.com"

    # 密码加密落库，明文不落表
    async with session_factory() as session:
        row = await session.get(AppSetting, "smtp_password")
    assert row is not None
    assert row.value != MAIL_PAYLOAD["smtp_password"]


@pytest.mark.asyncio
async def test_blank_password_keeps_existing(
    admin_api: tuple[AsyncClient, SessionFactory],
) -> None:
    """再次编辑时密码留空 / 不传 = 不修改，而非把密码置空。"""
    client, session_factory = admin_api
    await client.put("/api/system/settings/mail", json=MAIL_PAYLOAD)
    async with session_factory() as session:
        before = (await session.get(AppSetting, "smtp_password")).value

    # 1) 完全不传 smtp_password
    payload = {k: v for k, v in MAIL_PAYLOAD.items() if k != "smtp_password"}
    payload["smtp_port"] = 587
    r = await client.put("/api/system/settings/mail", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["smtp_secret_set"] is True
    assert r.json()["smtp_port"] == 587
    async with session_factory() as session:
        after = (await session.get(AppSetting, "smtp_password")).value
    assert after == before, "密码留空保存不应改动已存密码"

    # 2) 显式传空串同样视为不修改，而不是清空密码
    r = await client.put(
        "/api/system/settings/mail", json={**payload, "smtp_password": ""}
    )
    assert r.status_code == 200, r.text
    assert r.json()["smtp_secret_set"] is True
    assert r.json()["smtp_ready"] is True
    assert effective_settings().smtp_password


@pytest.mark.asyncio
async def test_ai_update_and_blank_key(
    admin_api: tuple[AsyncClient, SessionFactory],
) -> None:
    client, session_factory = admin_api
    r = await client.put(
        "/api/system/settings/ai",
        json={
            "ai_base_url": "https://ai.example.com/v1",
            "ai_model": "my-model",
            "ai_api_key": "sk-test-key",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ai_base_url"] == "https://ai.example.com/v1"
    assert body["ai_model"] == "my-model"
    assert body["ai_ready"] is True
    assert body["ai_secret_set"] is True
    assert "sk-test-key" not in r.text

    async with session_factory() as session:
        before = (await session.get(AppSetting, "ai_api_key")).value
    assert before != "sk-test-key"

    # 不传 api_key：Key 不变，其余字段照常更新
    r = await client.put(
        "/api/system/settings/ai",
        json={"ai_base_url": "https://ai.example.com/v1", "ai_model": "my-model-2"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ai_secret_set"] is True
    assert effective_settings().ai_model == "my-model-2"
    async with session_factory() as session:
        after = (await session.get(AppSetting, "ai_api_key")).value
    assert after == before