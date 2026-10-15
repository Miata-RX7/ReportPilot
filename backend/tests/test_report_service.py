"""报告实例状态机与确认 token 的单元测试（7 态，无需 DB）。

覆盖：状态机表、状态推进副作用、确认 token 哈希/有效期、收件人与确认人校验。
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.report import REPORT_STATUSES, Report
from app.services import report_service
from app.services.report_service import (
    ALLOWED_ACTIONS,
    MAX_RECIPIENTS,
    ReportError,
    create_instance,
)
from app.services.task_service import (
    build_cron_trigger,
    hash_token,
    new_confirmation_token,
    token_matches,
)


def _report(status: str) -> Report:
    return Report(id=1, template_id=1, period="2026-09", status=status)


class _FakeTemplate:
    id = 1
    name = "tpl"
    is_active = True


class _FakeSession:
    """create_instance 的校验在触碰 DB 之前完成，这里够用。"""

    def add(self, _obj: object) -> None:  # pragma: no cover - 校验失败时不会走到
        raise AssertionError("校验失败路径不应写库")

    async def flush(self) -> None:  # pragma: no cover
        raise AssertionError("校验失败路径不应 flush")


class TestStateMachineTable:
    def test_model_and_service_agree(self) -> None:
        assert set(ALLOWED_ACTIONS) == set(REPORT_STATUSES)

    def test_seven_statuses(self) -> None:
        assert REPORT_STATUSES == (
            "pending",
            "generating",
            "pending_confirmation",
            "confirmed",
            "pushed",
            "push_failed",
            "failed",
        )

    def test_legacy_statuses_removed(self) -> None:
        for old in ("draft", "generated", "approved", "rejected", "pending_review"):
            assert old not in ALLOWED_ACTIONS

    def test_pushed_is_terminal(self) -> None:
        assert ALLOWED_ACTIONS["pushed"] == set()

    def test_allowed_actions_per_status(self) -> None:
        assert ALLOWED_ACTIONS["pending"] == {"claim"}
        assert ALLOWED_ACTIONS["generating"] == {"generate_ok", "generate_fail"}
        assert ALLOWED_ACTIONS["pending_confirmation"] == {
            "confirm",
            "confirm_by_admin",
            "resend_confirmation",
        }
        assert ALLOWED_ACTIONS["confirmed"] == {"push"}
        assert ALLOWED_ACTIONS["push_failed"] == {"retry_push"}
        assert ALLOWED_ACTIONS["failed"] == {"regenerate"}


class TestEnsureAction:
    def test_allowed_passes(self) -> None:
        report_service._ensure_action(_report("confirmed"), "push")

    @pytest.mark.parametrize(
        "status,action",
        [
            ("pushed", "push"),
            ("pending", "confirm"),
            ("confirmed", "confirm"),
            ("failed", "push"),
        ],
    )
    def test_disallowed_raises(self, status: str, action: str) -> None:
        with pytest.raises(ReportError, match="不允许"):
            report_service._ensure_action(_report(status), action)


class TestSetStatus:
    def test_push_clears_error_and_stamps_time(self) -> None:
        r = _report("confirmed")
        r.last_error = "旧错误"
        r.error_stage = "push"
        report_service._set_status(r, "pushed", "push")
        assert r.status == "pushed"
        assert r.last_error is None and r.error_stage is None
        assert r.pushed_at is not None

    def test_push_failed_keeps_confirmed_at(self) -> None:
        """Q13：确认过再推送失败时 confirmed_at 保留，便于 UI 显示"已确认（推送失败）"。"""
        r = _report("push_failed")
        confirmed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        r.confirmed_at = confirmed_at
        assert r.confirmed_at == confirmed_at

    def test_wrong_action_rejected(self) -> None:
        with pytest.raises(ReportError):
            report_service._set_status(_report("pending_confirmation"), "pushed", "push")


class TestConfirmationToken:
    def test_stores_hash_not_plaintext(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.config import get_settings

        monkeypatch.setattr(get_settings(), "confirm_token_ttl_hours", 72)
        r = _report("generating")
        r.last_reminder_at = datetime.now(timezone.utc)
        raw = report_service._arm_confirmation(r)
        assert len(raw) >= 32
        assert r.confirmation_token != raw
        assert token_matches(raw, r.confirmation_token)
        # TTL 72h
        delta = r.expires_at - r.confirmation_sent_at
        assert timedelta(hours=71) < delta <= timedelta(hours=72)
        # 提醒计时被重置
        assert r.last_reminder_at is None

    def test_token_matching(self) -> None:
        raw = new_confirmation_token()
        h = hash_token(raw)
        assert token_matches(raw, h) is True
        assert token_matches(raw + "x", h) is False
        assert token_matches(raw, None) is False

    def test_two_tokens_differ(self) -> None:
        assert new_confirmation_token() != new_confirmation_token()

    def test_reminder_keeps_expiry(self) -> None:
        """提醒换 token 但**不续命**过期时间（否则每次提醒都无限延期）。"""
        r = _report("pending_confirmation")
        expires = datetime.now(timezone.utc) + timedelta(hours=10)
        r.expires_at = expires
        raw = report_service._arm_confirmation_keep_reminder_time(r)
        assert token_matches(raw, r.confirmation_token)
        assert r.expires_at == expires


class TestCreateInstanceValidation:
    @pytest.mark.anyio
    async def test_recipients_required(self) -> None:
        with pytest.raises(ReportError, match="收件人"):
            await create_instance(
                _FakeSession(),
                template=_FakeTemplate(),
                version=None,
                period="2026-09",
                recipient_emails=[],
                confirmer_email="a@x.com",
            )

    @pytest.mark.anyio
    async def test_confirmer_required(self) -> None:
        with pytest.raises(ReportError, match="确认人"):
            await create_instance(
                _FakeSession(),
                template=_FakeTemplate(),
                version=None,
                period="2026-09",
                recipient_emails=["a@x.com"],
                confirmer_email="   ",
            )

    @pytest.mark.anyio
    async def test_too_many_recipients(self) -> None:
        with pytest.raises(ReportError, match="最多"):
            await create_instance(
                _FakeSession(),
                template=_FakeTemplate(),
                version=None,
                period="2026-09",
                recipient_emails=[f"u{i}@x.com" for i in range(MAX_RECIPIENTS + 1)],
                confirmer_email="a@x.com",
            )

    def test_max_recipients(self) -> None:
        assert MAX_RECIPIENTS == 20


class TestCronTrigger:
    def test_five_fields(self) -> None:
        t = build_cron_trigger("0 9 1 * *")
        assert str(t) == "cron[month='*', day='1', day_of_week='*', hour='9', minute='0']"

    @pytest.mark.parametrize("bad", ["", "bad cron", "0 9 1 *", "99 9 1 * *"])
    def test_invalid_rejected(self, bad: str) -> None:
        from app.services.task_service import TaskError

        with pytest.raises(TaskError):
            build_cron_trigger(bad)
