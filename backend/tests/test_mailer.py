"""mailer 单元测试（mock aiosmtplib.send）。"""

from email.message import EmailMessage
from pathlib import Path

import aiosmtplib
import pytest

from app.config import get_settings
from app.services.mailer import MailerError, send_report_email


@pytest.fixture
def smtp_settings(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(settings, "smtp_port", 25)
    monkeypatch.setattr(settings, "smtp_from", "reportpilot@test")
    monkeypatch.setattr(settings, "smtp_username", "")
    monkeypatch.setattr(settings, "smtp_password", "")
    monkeypatch.setattr(settings, "smtp_use_tls", False)
    return settings


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[EmailMessage, dict]]:
    captured: list[tuple[EmailMessage, dict]] = []

    async def fake_send(msg: EmailMessage, **kwargs):  # type: ignore[no-untyped-def]
        captured.append((msg, kwargs))

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    return captured


@pytest.fixture
def docx_file(tmp_path: Path) -> Path:
    path = tmp_path / "report.docx"
    path.write_bytes(b"PK\x03\x04fake-docx-content")
    return path


class TestSend:
    async def test_message_composition(
        self, smtp_settings, sent, docx_file: Path
    ) -> None:
        await send_report_email(
            ["a@x.com", "b@x.com"], "月报模板", "2026-09", docx_file
        )
        msg, kwargs = sent[0]
        assert msg["Subject"] == "【ReportPilot】月报模板 2026-09 月报"
        assert msg["From"] == "reportpilot@test"
        assert msg["To"] == "a@x.com, b@x.com"
        attachments = list(msg.iter_attachments())
        assert len(attachments) == 1
        assert attachments[0].get_filename() == "月报模板_2026-09.docx"
        assert attachments[0].get_content() == b"PK\x03\x04fake-docx-content"
        assert kwargs["hostname"] == "smtp.test"
        assert kwargs["port"] == 25

    async def test_no_auth_when_password_empty(
        self, smtp_settings, sent, docx_file: Path
    ) -> None:
        await send_report_email(["a@x.com"], "月报模板", "2026-09", docx_file)
        _, kwargs = sent[0]
        assert "username" not in kwargs
        assert "password" not in kwargs

    async def test_auth_when_password_present(
        self, smtp_settings, sent, docx_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(smtp_settings, "smtp_username", "user@test")
        monkeypatch.setattr(smtp_settings, "smtp_password", "secret")
        await send_report_email(["a@x.com"], "月报模板", "2026-09", docx_file)
        _, kwargs = sent[0]
        assert kwargs["username"] == "user@test"
        assert kwargs["password"] == "secret"

    async def test_smtp_not_configured(
        self, docx_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(get_settings(), "smtp_host", "")
        with pytest.raises(MailerError, match="SMTP 未配置"):
            await send_report_email(["a@x.com"], "月报模板", "2026-09", docx_file)

    async def test_send_failure_wrapped(
        self, smtp_settings, docx_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def failing_send(msg, **kwargs):  # type: ignore[no-untyped-def]
            raise aiosmtplib.SMTPConnectError("Service not available")

        monkeypatch.setattr(aiosmtplib, "send", failing_send)
        with pytest.raises(MailerError, match="SMTP 发送失败"):
            await send_report_email(["a@x.com"], "月报模板", "2026-09", docx_file)
