"""SMTP 邮件：报告附件 + 确认链接 + 未确认提醒。

仅一套 SMTP 配置：`.env` 为默认值，系统设置页保存的覆盖项优先
（见 services.runtime_config）。密码为空则跳过认证（本地中继常见），
密码本身不落日志、不经接口返回。
"""

import logging
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import aiosmtplib

from app.services.runtime_config import effective_settings
from app.services.period_utils import report_kind
from app.services.task_service import fmt_hk

logger = logging.getLogger(__name__)

DOCX_SUBTYPE = "vnd.openxmlformats-officedocument.wordprocessingml.document"


class MailerError(Exception):
    """邮件发送失败。消息不含 SMTP 密码等敏感信息。"""


def _base_kwargs() -> dict[str, Any]:
    settings = effective_settings()
    if not settings.smtp_host:
        raise MailerError("SMTP 未配置（SMTP_HOST 为空）")
    kwargs: dict[str, Any] = {
        "hostname": settings.smtp_host,
        "port": settings.smtp_port,
        "use_tls": settings.smtp_use_tls,
    }
    if settings.smtp_password:
        kwargs["username"] = settings.smtp_username
        kwargs["password"] = settings.smtp_password
    return kwargs


def _from_address() -> str:
    settings = effective_settings()
    return settings.smtp_from or settings.smtp_username or "reportpilot@localhost"


def _contact_hint() -> str:
    """确认链接过期时，提示联系谁重新发送。"""
    return effective_settings().task_contact_email.strip()


async def send_report_email(
    recipients: list[str],
    report_name: str,
    period: str,
    attachment_path: Path,
) -> None:
    """发送报告邮件（docx 附件）。失败抛 MailerError。"""
    if not recipients:
        raise MailerError("接收人列表为空")

    msg = EmailMessage()
    msg["Subject"] = f"【ReportPilot】{report_name} {period} {report_kind(period)}"
    msg["From"] = _from_address()
    msg["To"] = ", ".join(recipients)
    msg.set_content(
        f"{report_name}（{period}）{report_kind(period)}已生成并通过确认，详见附件。\n\n"
        "—— ReportPilot 报告自动化系统"
    )
    msg.add_attachment(
        attachment_path.read_bytes(),
        maintype="application",
        subtype=DOCX_SUBTYPE,
        filename=f"{report_name}_{period}.docx",
    )
    try:
        await aiosmtplib.send(msg, **_base_kwargs())
    except Exception as exc:
        raise MailerError(f"SMTP 发送失败: {type(exc).__name__}: {exc}") from exc
    logger.info("报告邮件已发送 recipients=%d period=%s", len(recipients), period)


def _confirmation_body(
    report_name: str, period: str, confirm_url: str, expires_at: datetime | None
) -> str:
    hint = _contact_hint()
    contact = f"过期后请联系 {hint} 重新发送。" if hint else "过期后请联系管理员重新发送。"
    return (
        f"{report_name}（{period}）已生成，等待你确认后发送给最终收件人。\n\n"
        f"确认链接：{confirm_url}\n\n"
        f"此链接有效期至 {fmt_hk(expires_at)}（香港时间）。{contact}\n"
        "链接仅可使用一次。\n\n"
        "—— ReportPilot 报告自动化系统"
    )


async def send_confirmation_email(
    *,
    to: str,
    report_name: str,
    period: str,
    confirm_url: str,
    expires_at: datetime | None,
) -> None:
    """发送确认邮件（无附件，含一次性确认链接 + 有效期说明）。"""
    if not to:
        raise MailerError("确认人邮箱为空")
    msg = EmailMessage()
    msg["Subject"] = (
        f"【ReportPilot】请确认{report_kind(period)}：{report_name} {period}"
    )
    msg["From"] = _from_address()
    msg["To"] = to
    msg.set_content(_confirmation_body(report_name, period, confirm_url, expires_at))
    try:
        await aiosmtplib.send(msg, **_base_kwargs())
    except Exception as exc:
        raise MailerError(f"SMTP 发送失败: {type(exc).__name__}: {exc}") from exc
    logger.info("确认邮件已发送 to=%s period=%s", _mask(to), period)


async def send_reminder_email(
    *,
    to: str,
    report_name: str,
    period: str,
    confirm_url: str,
    expires_at: datetime | None,
) -> None:
    """未确认提醒邮件（每 24h 一次，直到链接过期）。"""
    if not to:
        raise MailerError("确认人邮箱为空")
    msg = EmailMessage()
    msg["Subject"] = (
        f"【ReportPilot】{report_kind(period)}待确认提醒：{report_name} {period}"
    )
    msg["From"] = _from_address()
    msg["To"] = to
    msg.set_content(
        f"{report_name}（{period}）仍未收到你的确认，报告尚未发送给最终收件人。\n\n"
        f"确认链接：{confirm_url}\n\n"
        f"此链接有效期至 {fmt_hk(expires_at)}（香港时间）。\n\n"
        "—— ReportPilot 报告自动化系统"
    )
    try:
        await aiosmtplib.send(msg, **_base_kwargs())
    except Exception as exc:
        raise MailerError(f"SMTP 发送失败: {type(exc).__name__}: {exc}") from exc
    logger.info("提醒邮件已发送 to=%s period=%s", _mask(to), period)


def _mask(email: str) -> str:
    """日志里脱敏邮箱（避免 PII 进日志）。"""
    if "@" not in email:
        return "***"
    name, domain = email.split("@", 1)
    head = name[:2] if len(name) > 2 else name[:1]
    return f"{head}***@{domain}"
