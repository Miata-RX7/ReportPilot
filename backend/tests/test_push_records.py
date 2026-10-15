"""推送记录（push_records）测试：成功/失败都写、attempt 递增、查询过滤。

用例把测试 session 显式传给 push_confirmed(db=...)，避免连到真实库。
"""

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
from app.models.report import PushRecord, Report
from app.models.template import WordTemplate
from app.models.user import User
from app.services import report_service
from app.services.mailer import MailerError


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
        yield type("Ctx", (), {"client": client, "session_factory": factory, "user": user})
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed(factory, tmp_path, status: str = "confirmed") -> int:
    f = tmp_path / "report_1_2026-09.docx"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"PK\x03\x04dummy")
    async with factory() as s:
        s.add(WordTemplate(id=1, name="运维月报模板", file_path="x", is_active=True))
        rep = Report(
            template_id=1,
            period="2026-09",
            status=status,
            source="manual",
            recipient_emails=["a@x.com", "b@x.com"],
            confirmer_email="c@x.com",
            file_path=str(f),
            created_by=1,
        )
        s.add(rep)
        await s.commit()
        await s.refresh(rep)
        return rep.id


def _patch_mail(monkeypatch, fail: str | None = None) -> None:
    async def fake_send(recipients, report_name, period, path):
        if fail:
            raise MailerError(fail)

    monkeypatch.setattr(report_service, "send_report_email", fake_send)


async def _push(factory, rid: int, action: str = "push") -> tuple[bool, str]:
    async with factory() as s:
        return await report_service.push_confirmed(rid, actor_id=1, action=action, db=s)


async def _status(factory, rid: int) -> str:
    async with factory() as s:
        return (await s.get(Report, rid)).status


async def _records(factory, rid: int) -> list[PushRecord]:
    async with factory() as s:
        return list(
            (
                await s.scalars(
                    select(PushRecord)
                    .where(PushRecord.report_id == rid)
                    .order_by(PushRecord.attempt)
                )
            ).all()
        )


class TestPushConfirmed:
    @pytest.mark.anyio
    async def test_success_records_push(self, ctx, tmp_path, monkeypatch) -> None:
        rid = await _seed(ctx.session_factory, tmp_path)
        _patch_mail(monkeypatch)
        ok, err = await _push(ctx.session_factory, rid)
        assert ok is True and err == ""
        assert await _status(ctx.session_factory, rid) == "pushed"
        recs = await _records(ctx.session_factory, rid)
        assert len(recs) == 1
        assert recs[0].status == "success" and recs[0].attempt == 1
        assert recs[0].recipient_count == 2
        assert recs[0].attachment_name == "report_1_2026-09.docx"

    @pytest.mark.anyio
    async def test_failure_sets_push_failed(self, ctx, tmp_path, monkeypatch) -> None:
        rid = await _seed(ctx.session_factory, tmp_path)
        _patch_mail(monkeypatch, fail="SMTP 超时")
        ok, err = await _push(ctx.session_factory, rid)
        assert ok is False and "SMTP" in err
        assert await _status(ctx.session_factory, rid) == "push_failed"
        recs = await _records(ctx.session_factory, rid)
        assert recs[0].status == "failed" and recs[0].error

    @pytest.mark.anyio
    async def test_retry_increments_attempt(self, ctx, tmp_path, monkeypatch) -> None:
        rid = await _seed(ctx.session_factory, tmp_path)
        _patch_mail(monkeypatch, fail="连接失败")
        await _push(ctx.session_factory, rid)
        _patch_mail(monkeypatch)
        ok, _ = await _push(ctx.session_factory, rid, action="retry_push")
        assert ok is True
        recs = await _records(ctx.session_factory, rid)
        assert [r.attempt for r in recs] == [1, 2]
        assert [r.status for r in recs] == ["failed", "success"]
        assert [r.action for r in recs] == ["push", "retry_push"]

    @pytest.mark.anyio
    async def test_cannot_push_wrong_status(self, ctx, tmp_path, monkeypatch) -> None:
        rid = await _seed(ctx.session_factory, tmp_path, status="pending_confirmation")
        _patch_mail(monkeypatch)
        ok, err = await _push(ctx.session_factory, rid)
        assert ok is False and "不允许" in err
        assert await _records(ctx.session_factory, rid) == []


class TestPushRecordApi:
    @pytest.mark.anyio
    async def test_list_includes_report_name(self, ctx, tmp_path, monkeypatch) -> None:
        rid = await _seed(ctx.session_factory, tmp_path)
        _patch_mail(monkeypatch)
        await _push(ctx.session_factory, rid)
        r = await ctx.client.get("/api/push-records")
        assert r.status_code == 200
        item = r.json()["items"][0]
        assert item["report_name"] == "运维月报模板_2026-09"
        assert item["status"] == "success"

    @pytest.mark.anyio
    async def test_status_filter(self, ctx, tmp_path, monkeypatch) -> None:
        rid = await _seed(ctx.session_factory, tmp_path)
        _patch_mail(monkeypatch, fail="失败")
        await _push(ctx.session_factory, rid)
        assert (await ctx.client.get("/api/push-records", params={"status": "failed"})).json()["total"] == 1
        assert (await ctx.client.get("/api/push-records", params={"status": "success"})).json()["total"] == 0


# --------------------------------------------------------------------------
# 确认邮件 / 催办提醒也要留痕（曾经"用户收到邮件但推送记录一片空白"）
# --------------------------------------------------------------------------
def _patch_confirmation_mail(
    monkeypatch, name: str, fail: str | None = None
) -> list[dict]:
    """桩掉 mailer 里的确认/提醒发信（函数内是 import 到模块属性，须按模块打桩）。"""
    from app.services import mailer

    sent: list[dict] = []

    async def fake_send(*, to, report_name, period, confirm_url, expires_at):
        if fail:
            raise MailerError(fail)
        sent.append({"to": to, "report_name": report_name, "period": period})

    monkeypatch.setattr(mailer, name, fake_send)
    return sent


class TestConfirmationMailRecords:
    @pytest.mark.anyio
    async def test_confirmation_success_recorded(self, ctx, tmp_path, monkeypatch) -> None:
        rid = await _seed(ctx.session_factory, tmp_path, status="pending_confirmation")
        monkeypatch.setattr(report_service, "AsyncSessionLocal", ctx.session_factory)
        sent = _patch_confirmation_mail(
            monkeypatch, "send_confirmation_email"
        )
        await report_service._deliver_confirmation_mail(rid, "raw-token", reminder=False)
        recs = await _records(ctx.session_factory, rid)
        assert len(recs) == 1
        assert recs[0].action == "confirmation"
        assert recs[0].status == "success"
        assert recs[0].attempt == 1
        # 收件人 = 确认人，无附件
        assert recs[0].recipients == ["c@x.com"]
        assert recs[0].recipient_count == 1
        assert recs[0].attachment_name is None
        assert sent[0]["to"] == "c@x.com"

    @pytest.mark.anyio
    async def test_confirmation_failure_recorded_and_reraised(
        self, ctx, tmp_path, monkeypatch
    ) -> None:
        rid = await _seed(ctx.session_factory, tmp_path, status="pending_confirmation")
        monkeypatch.setattr(report_service, "AsyncSessionLocal", ctx.session_factory)
        _patch_confirmation_mail(monkeypatch, "send_confirmation_email", fail="SMTP 拒信")
        with pytest.raises(MailerError):
            await report_service._deliver_confirmation_mail(rid, "raw-token", reminder=False)
        recs = await _records(ctx.session_factory, rid)
        assert len(recs) == 1
        assert recs[0].action == "confirmation"
        assert recs[0].status == "failed"
        assert recs[0].error and "SMTP" in recs[0].error

    @pytest.mark.anyio
    async def test_reminder_recorded_with_reminder_action(
        self, ctx, tmp_path, monkeypatch
    ) -> None:
        rid = await _seed(ctx.session_factory, tmp_path, status="pending_confirmation")
        monkeypatch.setattr(report_service, "AsyncSessionLocal", ctx.session_factory)
        _patch_confirmation_mail(monkeypatch, "send_reminder_email")
        await report_service._deliver_confirmation_mail(rid, "raw-token", reminder=True)
        recs = await _records(ctx.session_factory, rid)
        assert [r.action for r in recs] == ["reminder"]

    @pytest.mark.anyio
    async def test_confirmation_then_push_attempt_sequence(
        self, ctx, tmp_path, monkeypatch
    ) -> None:
        """确认信占 attempt=1，推送顺延为 2（序号 = 该报告邮件发送序号）。"""
        rid = await _seed(ctx.session_factory, tmp_path, status="pending_confirmation")
        monkeypatch.setattr(report_service, "AsyncSessionLocal", ctx.session_factory)
        _patch_confirmation_mail(monkeypatch, "send_confirmation_email")
        await report_service._deliver_confirmation_mail(rid, "raw-token", reminder=False)
        async with ctx.session_factory() as s:
            rep = await s.get(Report, rid)
            rep.status = "confirmed"
            await s.commit()
        _patch_mail(monkeypatch)
        ok, _ = await _push(ctx.session_factory, rid)
        assert ok is True
        recs = await _records(ctx.session_factory, rid)
        assert [r.attempt for r in recs] == [1, 2]
        assert [r.action for r in recs] == ["confirmation", "push"]

    @pytest.mark.anyio
    async def test_action_filter_confirmation(self, ctx, tmp_path, monkeypatch) -> None:
        rid = await _seed(ctx.session_factory, tmp_path, status="pending_confirmation")
        monkeypatch.setattr(report_service, "AsyncSessionLocal", ctx.session_factory)
        _patch_confirmation_mail(monkeypatch, "send_confirmation_email")
        await report_service._deliver_confirmation_mail(rid, "raw-token", reminder=False)
        r = await ctx.client.get("/api/push-records", params={"action": "confirmation"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["action"] == "confirmation"
        assert (
            await ctx.client.get("/api/push-records", params={"action": "reminder"})
        ).json()["total"] == 0
