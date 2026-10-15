"""管理员代确认（平台内确认，无需邮件链接）。"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_current_user
from app.database import get_db
from app.main import app
from app.models.base import Base
from app.models.report import Report, ReportAudit
from app.models.template import WordTemplate
from app.models.user import User


def _user(uid: int, role: str, email: str | None = None) -> User:
    return User(
        id=uid,
        username=f"u{uid}",
        email=email or f"u{uid}@test.local",
        display_name=f"u{uid}",
        source="local",
        role=role,
        is_active=True,
    )


@pytest_asyncio.fixture
async def env(monkeypatch: pytest.MonkeyPatch):
    from app.services import report_service

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as s:
            yield s

    async with factory() as s:
        # 用户必须真实落库：confirm_by_user 会按 id 查 User 比对邮箱
        s.add(_user(1, "admin", email="admin@test.local"))
        s.add(_user(7, "user", email="alice@test.local"))
        s.add(_user(8, "user", email="bob@test.local"))
        tpl = WordTemplate(
            name="tpl", owner_id=1, placeholders_json=[], current_version_id=None
        )
        s.add(tpl)
        await s.flush()
        rep = Report(
            template_id=tpl.id,
            source="cron",
            period="2026-09",
            status="pending_confirmation",
            recipient_emails=["a@b.com"],
            confirmer_email="alice@test.local",
            facts_json=[],
            insights_json={},
        )
        s.add(rep)
        await s.commit()
        report_id = rep.id

    # 服务层自建 session（confirm_by_admin）与发信都要走测试库/桩
    monkeypatch.setattr(report_service, "AsyncSessionLocal", factory)

    async def _fake_send(*args, **kwargs):
        return None

    monkeypatch.setattr(report_service, "send_report_email", _fake_send)

    def _client(user, login=True):
        app.dependency_overrides[get_db] = _get_db
        if login:
            app.dependency_overrides[get_current_user] = lambda: user
        else:
            app.dependency_overrides.pop(get_current_user, None)
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield report_id, factory, _client
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_can_confirm_on_behalf(env):
    report_id, factory, make = env
    async with make(_user(1, "admin", email="admin@test.local")) as c:
        r = await c.post(f"/api/reports/{report_id}/confirm")
    # SMTP 未配置时推送会失败，但确认本身必须成功（状态已落库）
    assert r.status_code == 200, r.text

    async with factory() as s:
        rep = await s.get(Report, report_id)
        assert rep.confirmation_token is None
        assert rep.status in ("confirmed", "push_failed")
        audits = list(
            (await s.scalars(select(ReportAudit).where(ReportAudit.report_id == report_id))).all()
        )
        actions = [a.action for a in audits]
        assert "confirm_by_admin" in actions
        rec = next(a for a in audits if a.action == "confirm_by_admin")
        assert rec.auditor_id == 1
        assert "代确认" in (rec.comment or "")


@pytest.mark.asyncio
async def test_non_admin_forbidden(env):
    report_id, _factory, make = env
    async with make(_user(2, "viewer", email="viewer@test.local")) as c:
        r = await c.post(f"/api/reports/{report_id}/confirm")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_confirm_twice_conflicts(env):
    report_id, _factory, make = env
    async with make(_user(1, "admin", email="admin@test.local")) as c:
        await c.post(f"/api/reports/{report_id}/confirm")
        r = await c.post(f"/api/reports/{report_id}/confirm")
    # 重复确认是状态冲突 -> 409
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    # 首次确认后状态已离开 pending_confirmation（推送失败时为 push_failed），
    # 两种情况都应被 409 拒绝并给出可读原因
    assert "不允许确认" in detail or "已确认" in detail, detail


@pytest.mark.asyncio
async def test_confirm_requires_login(env):
    report_id, _factory, make = env
    async with make(_user(1, "admin", email="admin@test.local"), login=False) as c:
        r = await c.post(f"/api/reports/{report_id}/confirm")
    assert r.status_code == 401

@pytest.mark.asyncio
async def test_confirmer_can_confirm_own_report(env):
    """确认人本人（邮箱匹配）可确认，不需管理员权限、不需 token。"""
    report_id, factory, make = env
    async with make(_user(7, "user", email="alice@test.local")) as c:
        r = await c.post(f"/api/reports/{report_id}/confirm")
    assert r.status_code == 200, r.text
    async with factory() as s_:
        audits = list(
            (
                await s_.scalars(
                    select(ReportAudit).where(ReportAudit.report_id == report_id)
                )
            ).all()
        )
        actions = [a.action for a in audits]
        assert "confirm" in actions, "本人确认应记confirm"
        assert "confirm_by_admin" not in actions, "本人确认不应记成代确认"
        rec = next(a for a in audits if a.action == "confirm")
        assert rec.auditor_id == 7
        assert "平台内确认" in (rec.comment or "")


@pytest.mark.asyncio
async def test_other_user_cannot_confirm(env):
    """既非管理员又非确认人 -> 403。"""
    report_id, _f, make = env
    async with make(_user(8, "bob")) as c:
        r = await c.post(f"/api/reports/{report_id}/confirm")
    assert r.status_code == 403
    assert "确认人" in r.json()["detail"]


@pytest.mark.asyncio
async def test_non_admin_cannot_proxy_confirm(env):
    """普通用户即便邮箱不是确认人，也不能代确认（代确认仅管理员）。"""
    report_id, _f, make = env
    async with make(_user(8, "bob")) as c:
        assert (await c.post(f"/api/reports/{report_id}/confirm")).status_code == 403
