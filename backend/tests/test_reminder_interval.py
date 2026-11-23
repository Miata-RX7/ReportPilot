"""确认邮件发送频率（reminder_interval_hours）可配置并生效（2026-11-23）。"""

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
from app.models.user import User


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

    user = User(
        id=1,
        username="tester",
        email="tester@test.local",
        display_name="Tester",
        source="local",
        role="admin",
        is_active=True,
    )
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, session_factory
    app.dependency_overrides.clear()
    await engine.dispose()


def _mail_payload(**extra) -> dict:
    return {
        "smtp_host": "smtp.example.com",
        "smtp_port": 465,
        "smtp_username": "u@example.com",
        "smtp_from": "rp@example.com",
        "smtp_use_tls": True,
        "app_base_url": "http://x",
        **extra,
    }


class TestReminderInterval:
    async def test_default_is_24_hours(self) -> None:
        """默认 24 小时 1 次（config.py 的默认值）。"""
        from app.config import get_settings

        assert get_settings().reminder_interval_hours == 24

    async def test_editable_via_runtime_config(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """页面可改：PUT /settings/mail 落库后读得回新值。"""
        client, session_factory = api
        r = await client.get("/api/system/settings")
        assert r.status_code == 200, r.text
        assert isinstance(r.json()["reminder_interval_hours"], int)

        r = await client.put(
            "/api/system/settings/mail",
            json=_mail_payload(reminder_interval_hours=6),
        )
        assert r.status_code == 200, r.text
        assert r.json()["reminder_interval_hours"] == 6

        from sqlalchemy import select

        from app.models.app_setting import AppSetting

        async with session_factory() as db:
            row = (
                await db.scalars(
                    select(AppSetting).where(
                        AppSetting.key == "reminder_interval_hours"
                    )
                )
            ).first()
            assert row is not None
            assert row.value == "6"

    async def test_rejects_out_of_range(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """只支持 *小时 1 次 -> 必须是 1..720 的正整数。"""
        client, _sf = api
        for bad in (0, -1, 721):
            r = await client.put(
                "/api/system/settings/mail",
                json=_mail_payload(reminder_interval_hours=bad),
            )
            assert r.status_code == 422, f"{bad} 应被拒绝，实际 {r.status_code}"

    async def test_sweep_uses_runtime_config(self) -> None:
        """sweep_reminders 必须读**运行时配置**，不是纯 .env 的 Settings。

        否则页面改了频率不生效 —— 这正是本次要修的点。
        """
        import inspect

        from app.services import scheduler

        src = inspect.getsource(scheduler.sweep_reminders)
        assert "effective_settings" in src, (
            "sweep_reminders 仍在用 get_settings()，页面改的频率不会生效"
        )
