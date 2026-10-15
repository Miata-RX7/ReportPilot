"""报告 API 集成测试（7 态 + 任务触发 + 一次性确认链接）。

替代旧的 8 态测试（submit/approve/reject 流程已删除）。
"""

import re

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.deps import get_current_user
from app.database import get_db
from app.main import app
from app.models import Base
from app.models.report import Report, ReportAudit
from app.models.template import TemplateVersion, WordTemplate
from app.models.user import User
from app.services import mailer, report_service

PENDING = "pending_confirmation"


@pytest_asyncio.fixture
async def ctx(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path / "storage"))
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _get_db():
        async with factory() as s:
            yield s

    user = User(
        id=1,
        username="ops",
        email="ops@x.com",
        display_name="ops",
        source="local",
        role="admin",
        is_active=True,
    )
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield type(
            "Ctx", (), {"client": client, "session_factory": factory, "user": user}
        )
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture(autouse=True)
def _use_test_sessions(ctx, monkeypatch):
    """服务层自建 session（confirm/push/run_generation）也走测试库。

    生产代码不该为了测试改签名，这里直接把 AsyncSessionLocal 换成测试的
    session_factory。
    """
    monkeypatch.setattr(report_service, "AsyncSessionLocal", ctx.session_factory)


@pytest.fixture(autouse=True)
def stub_mail(monkeypatch):
    """桩掉 SMTP：记录调用，避免真实发信。"""
    sent: list[dict] = []

    async def fake_report(recipients, report_name, period, attachment_path):
        sent.append({"kind": "report", "to": list(recipients), "period": period})

    async def fake_confirm(*, to, report_name, period, confirm_url, expires_at):
        sent.append({"kind": "confirmation", "to": to, "url": confirm_url})

    async def fake_remind(*, to, report_name, period, confirm_url, expires_at):
        sent.append({"kind": "reminder", "to": to, "url": confirm_url})

    monkeypatch.setattr(report_service, "send_report_email", fake_report)
    monkeypatch.setattr(mailer, "send_report_email", fake_report)
    monkeypatch.setattr(mailer, "send_confirmation_email", fake_confirm)
    monkeypatch.setattr(mailer, "send_reminder_email", fake_remind)
    return sent


async def _seed_template(factory, tmp_path, *, name: str = "运维月报模板") -> int:
    """建模板 + 版本 + 一个占位 docx（内容不合法也没关系，渲染会失败->failed）。"""
    f = tmp_path / f"{name}.docx"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"PK\x03\x04not-a-real-docx")
    async with factory() as s:
        tpl = WordTemplate(id=1, name=name, file_path=str(f), is_active=True)
        s.add(tpl)
        await s.flush()
        ver = TemplateVersion(
            template_id=1,
            version_no=1,
            file_path=str(f),
            placeholders_json=[],
            source="upload",
        )
        s.add(ver)
        await s.flush()
        # get_current_version 按 current_version_id 找，必须指回当前版本
        tpl.current_version_id = ver.id
        await s.commit()
        return tpl.id


async def _make_report(factory, tmp_path, **kw) -> int:
    """直接落一条实例（绕过生成）。"""
    f = tmp_path / "out.docx"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"PK\x03\x04x")
    data = {
        "template_id": 1,
        "period": "2026-09",
        "status": PENDING,
        "source": "manual",
        "recipient_emails": ["a@x.com"],
        "confirmer_email": "c@x.com",
        "file_path": str(f),
        "created_by": 1,
        "confirmation_token": "h" * 64,
        "confirmation_sent_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    }
    data.update(kw)
    async with factory() as s:
        rep = Report(**data)
        s.add(rep)
        await s.commit()
        await s.refresh(rep)
        return rep.id


class TestCreate:
    @pytest.mark.anyio
    async def test_create_requires_confirmer(self, ctx) -> None:
        r = await ctx.client.post(
            "/api/reports",
            json={
                "template_id": 1,
                "period": "2026-09",
                "recipient_emails": ["a@x.com"],
            },
        )
        assert r.status_code == 422
        assert "confirmer_email" in r.text

    @pytest.mark.anyio
    async def test_create_requires_existing_active_template(
        self, ctx, tmp_path
    ) -> None:
        r = await ctx.client.post(
            "/api/reports",
            json={
                "template_id": 999,
                "period": "2026-09",
                "recipient_emails": ["a@x.com"],
                "confirmer_email": "c@x.com",
            },
        )
        assert r.status_code == 422
        assert "模板" in r.text

    @pytest.mark.anyio
    async def test_create_returns_pending_instance(self, ctx, tmp_path) -> None:
        tpl_id = await _seed_template(ctx.session_factory, tmp_path)
        r = await ctx.client.post(
            "/api/reports",
            json={
                "template_id": tpl_id,
                "period": "2026-09",
                "recipient_emails": ["a@x.com", "a@x.com"],
                "confirmer_email": "c@x.com",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "pending"
        assert body["task_id"] is None
        assert body["source"] == "manual"
        assert body["recipient_emails"] == ["a@x.com"]  # 去重


class TestListAndDetail:
    @pytest.mark.anyio
    async def test_list_and_filters(self, ctx, tmp_path) -> None:
        await _seed_template(ctx.session_factory, tmp_path)
        await _make_report(ctx.session_factory, tmp_path, period="2026-09")
        await _make_report(ctx.session_factory, tmp_path, period="2026-08", status="pushed")

        r = await ctx.client.get("/api/reports")
        assert r.status_code == 200
        assert len(r.json()) == 2
        # 来源：手动实例 task_name 为 null
        assert all(i["task_name"] is None for i in r.json())

        r2 = await ctx.client.get("/api/reports", params={"report_status": "pushed"})
        assert len(r2.json()) == 1
        r3 = await ctx.client.get("/api/reports", params={"period": "2026-09"})
        assert len(r3.json()) == 1

    @pytest.mark.anyio
    async def test_detail_includes_audits_and_push_records(
        self, ctx, tmp_path
    ) -> None:
        await _seed_template(ctx.session_factory, tmp_path)
        rid = await _make_report(ctx.session_factory, tmp_path)
        async with ctx.session_factory() as s:
            s.add(ReportAudit(report_id=rid, auditor_id=1, action="generate", comment="c"))
            await s.commit()
        r = await ctx.client.get(f"/api/reports/{rid}")
        assert r.status_code == 200
        body = r.json()
        assert body["audits"][0]["action"] == "generate"
        assert body["push_records"] == []

    @pytest.mark.anyio
    async def test_download_missing_file_404(self, ctx, tmp_path) -> None:
        await _seed_template(ctx.session_factory, tmp_path)
        rid = await _make_report(ctx.session_factory, tmp_path, file_path=None)
        r = await ctx.client.get(f"/api/reports/{rid}/download")
        assert r.status_code == 404


class TestConfirmFlow:
    @pytest.mark.anyio
    async def test_confirm_requires_valid_token(self, ctx, tmp_path) -> None:
        await _seed_template(ctx.session_factory, tmp_path)
        rid = await _make_report(ctx.session_factory, tmp_path)
        r = await ctx.client.post(
            f"/api/confirm/{rid}", json={"token": "wrong-token-1234"}
        )
        assert r.status_code == 409
        assert "无效" in r.text or "已使用" in r.text

    @pytest.mark.anyio
    async def test_confirm_and_push(self, ctx, tmp_path, stub_mail) -> None:
        await _seed_template(ctx.session_factory, tmp_path)
        rid = await _make_report(ctx.session_factory, tmp_path)
        from app.services.task_service import hash_token, new_confirmation_token

        async with ctx.session_factory() as s:
            rep = await s.get(Report, rid)
            raw = new_confirmation_token()
            rep.confirmation_token = hash_token(raw)
            await s.commit()

        r = await ctx.client.post(f"/api/confirm/{rid}", json={"token": raw})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["confirmed"] is True
        assert body["push_status"] == "pushed"
        # token 一次性
        async with ctx.session_factory() as s:
            rep = await s.get(Report, rid)
            assert rep.confirmation_token is None
            assert rep.status == "pushed"

    @pytest.mark.anyio
    async def test_reuse_token_conflicts(self, ctx, tmp_path) -> None:
        await _seed_template(ctx.session_factory, tmp_path)
        rid = await _make_report(ctx.session_factory, tmp_path)
        from app.services.task_service import hash_token

        async with ctx.session_factory() as s:
            rep = await s.get(Report, rid)
            raw = "valid-token-12345678"
            rep.confirmation_token = hash_token(raw)
            await s.commit()
        first = await ctx.client.post(f"/api/confirm/{rid}", json={"token": raw})
        assert first.status_code == 200
        second = await ctx.client.post(f"/api/confirm/{rid}", json={"token": raw})
        assert second.status_code == 409
        assert "已使用" in second.text

    @pytest.mark.anyio
    async def test_expired_token_rejected(self, ctx, tmp_path) -> None:
        from datetime import datetime, timedelta, timezone

        from app.services.task_service import hash_token

        await _seed_template(ctx.session_factory, tmp_path)
        rid = await _make_report(
            ctx.session_factory,
            tmp_path,
            confirmation_token=hash_token("expired-token-1234"),
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        r = await ctx.client.post(
            f"/api/confirm/{rid}", json={"token": "expired-token-1234"}
        )
        assert r.status_code == 409
        assert "过期" in r.text

    @pytest.mark.anyio
    async def test_confirm_preview_hides_fact_values(self, ctx, tmp_path) -> None:
        from app.services.task_service import hash_token

        await _seed_template(ctx.session_factory, tmp_path)
        token = "preview-token-1234"
        rid = await _make_report(
            ctx.session_factory,
            tmp_path,
            confirmation_token=hash_token(token),
            facts_json=[
                {
                    "metric_id": 1,
                    "name": "secret_metric",
                    "value": 999999,
                    "value_shape": "scalar",
                    "change_pct": None,
                }
            ],
        )
        r = await ctx.client.get(f"/api/confirm/{rid}", params={"token": token})
        assert r.status_code == 200
        body = r.json()
        assert body["token_state"] == "valid"
        # 不含具体数值
        assert "999999" not in r.text
        assert "secret_metric" not in r.text
        assert body["metric_count"] == 1


class TestResendAndRetry:
    @pytest.mark.anyio
    async def test_resend_confirmation_rotates_token(self, ctx, tmp_path, stub_mail) -> None:
        from app.services.task_service import hash_token

        await _seed_template(ctx.session_factory, tmp_path)
        old_token = "old-token-1234"
        rid = await _make_report(
            ctx.session_factory, tmp_path, confirmation_token=hash_token(old_token)
        )
        r = await ctx.client.post(f"/api/reports/{rid}/resend-confirmation")
        assert r.status_code == 204
        async with ctx.session_factory() as s:
            rep = await s.get(Report, rid)
            assert rep.confirmation_token != hash_token(old_token)
        assert any(m["kind"] == "confirmation" for m in stub_mail)

    @pytest.mark.anyio
    async def test_retry_push_requires_push_failed(self, ctx, tmp_path) -> None:
        await _seed_template(ctx.session_factory, tmp_path)
        rid = await _make_report(ctx.session_factory, tmp_path)
        r = await ctx.client.post(f"/api/reports/{rid}/retry-push")
        assert r.status_code == 422
        assert "不允许" in r.text

    @pytest.mark.anyio
    async def test_regenerate_requires_failed(self, ctx, tmp_path) -> None:
        await _seed_template(ctx.session_factory, tmp_path)
        rid = await _make_report(ctx.session_factory, tmp_path)
        r = await ctx.client.post(f"/api/reports/{rid}/regenerate")
        assert r.status_code == 422
        assert "不允许" in r.text
