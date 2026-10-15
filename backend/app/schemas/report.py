"""报告相关 Pydantic v2 schema（7 态状态机）。"""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.template import InsightAIConfig
from app.services.period_utils import PERIOD_PATTERN

# 与 models.report.REPORT_STATUSES 对齐
ReportStatus = Literal[
    "pending",
    "generating",
    "pending_confirmation",
    "confirmed",
    "pushed",
    "push_failed",
    "failed",
]


def _is_expired(status: str, expires_at: datetime | None) -> bool:
    """确认链接是否已过期（只对待确认状态有意义）。"""
    if status != "pending_confirmation" or expires_at is None:
        return False
    return expires_at < datetime.now(timezone.utc)


class ReportCreate(BaseModel):
    """报告页手动创建一次性实例（不走任务，但仍需确认人）。"""

    template_id: int
    period: str = Field(pattern=PERIOD_PATTERN)
    recipient_emails: list[EmailStr] = Field(min_length=1)
    confirmer_email: EmailStr


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int | None
    template_id: int
    version_id: int | None
    source: str
    period: str
    status: ReportStatus
    recipient_emails: list[str]
    confirmer_email: str | None
    file_path: str | None
    last_error: str | None
    error_stage: str | None
    confirmation_sent_at: datetime | None
    expires_at: datetime | None
    confirmed_at: datetime | None
    last_reminder_at: datetime | None
    pushed_at: datetime | None
    created_by: int | None
    created_at: datetime


class ReportListItem(ReportRead):
    template_name: str
    # 来源：任务触发时给任务名（前端可跳转），手动为 null
    task_name: str | None = None


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    comment: str | None
    auditor_id: int | None
    created_at: datetime


class PushRecordRead(BaseModel):
    """一条推送记录（发送事实）。"""

    id: int
    report_id: int
    # 报告名（模板名_周期），列表直接给出避免前端反查
    report_name: str
    attempt: int  # 该实例第几次发送（含确认/催办邮件）
    # push=推送报告附件；retry_push=推送失败后重试；
    # confirmation=确认邮件（含重新发送）；reminder=24h 未确认催办提醒
    action: Literal["push", "retry_push", "confirmation", "reminder"]
    status: Literal["success", "failed"]
    recipients: list[str]
    recipient_count: int
    attachment_name: str | None
    duration_ms: int | None
    error: str | None
    actor_id: int | None
    created_at: datetime


class ReportDetail(ReportRead):
    """facts_json 按 dict 原样返回（不做 Pydantic 强反序列化，向后兼容）。"""

    facts_json: list[dict[str, Any]]
    insights_json: dict[str, Any]
    audits: list[AuditRead]
    push_records: list[PushRecordRead] = []


class PushRecordList(BaseModel):
    total: int
    items: list[PushRecordRead]


# --------------------------------------------------------------------------
# AI 摘要：单段重生成
# --------------------------------------------------------------------------
class InsightRegenerateRequest(BaseModel):
    """单段摘要重生成请求：可选**临时覆盖**配置（不传则用模板当前绑定）。"""

    ai_config: InsightAIConfig | None = None


class InsightSlotResult(BaseModel):
    """单段摘要结果（重生成接口返回）。"""

    slot: str
    text: str
    unverified_numbers: list[str] = Field(default_factory=list)
    sample_leak: list[str] = Field(default_factory=list)
    degraded: bool
    retried: bool = False
    model: str = ""


# --------------------------------------------------------------------------
# 确认（公开接口，无需登录）
# --------------------------------------------------------------------------
class ConfirmPreview(BaseModel):
    """确认页概要。

    **刻意不含具体指标数值**：确认链接会出现在邮件里，被转发即等于泄露运营
    数据。只给报告名/周期/AI 摘要/指标数与异常数。
    """

    report_id: int
    report_name: str
    period: str
    status: ReportStatus
    confirmer_email: str
    recipient_emails: list[str]
    created_at: datetime
    expires_at: datetime | None
    is_expired: bool
    ai_summary: str | None = None
    metric_count: int = 0
    abnormal_count: int = 0
    # token 校验结果：valid / invalid / used / expired
    token_state: Literal["valid", "invalid", "used", "expired"]
    message: str | None = None


class ConfirmRequest(BaseModel):
    token: str = Field(min_length=8, max_length=128)


class ConfirmResult(BaseModel):
    """确认结果：确认已生效，推送结果一并回传。"""

    report_id: int
    confirmed: bool
    push_status: Literal["pushed", "push_failed"]
    push_error: str | None = None
    message: str
