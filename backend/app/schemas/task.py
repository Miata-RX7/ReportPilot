"""报告任务 API schema。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.services.period_utils import MAX_PERIOD_OFFSET


class ReportTaskBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    template_id: int
    # 5 段式 cron：分 时 日 月 周，如 "0 9 1 * *" = 每月 1 号 09:00
    cron_expr: str = Field(min_length=1, max_length=64)
    # 统计周期：粒度 month/week + 相对触发时刻的偏移（0=本期，-1=上一周期）
    period_granularity: Literal["month", "week"] = "month"
    period_offset: int = Field(default=0, ge=-MAX_PERIOD_OFFSET, le=0)
    confirmer_email: EmailStr
    recipient_emails: list[EmailStr] = Field(min_length=1)
    is_active: bool = True

    @field_validator("recipient_emails", mode="before")
    @classmethod
    def _clean(cls, v: object) -> object:
        """去空串 + 去重保序（前端 tag 输入可能带重复）。"""
        if not isinstance(v, (list, tuple)):
            return v
        out: list[str] = []
        for x in v:
            s = str(x).strip()
            if s and s not in out:
                out.append(s)
        return out


class ReportTaskCreate(ReportTaskBase):
    pass


class ReportTaskUpdate(BaseModel):
    """全量可选（按需局部更新）。cron 校验失败时任务与调度都保持原样。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    template_id: int | None = None
    cron_expr: str | None = Field(default=None, min_length=1, max_length=64)
    period_granularity: Literal["month", "week"] | None = None
    period_offset: int | None = Field(default=None, ge=-MAX_PERIOD_OFFSET, le=0)
    confirmer_email: EmailStr | None = None
    recipient_emails: list[EmailStr] | None = None
    is_active: bool | None = None

    @field_validator("recipient_emails", mode="before")
    @classmethod
    def _clean(cls, v: object) -> object:
        if v is None or not isinstance(v, (list, tuple)):
            return v
        out: list[str] = []
        for x in v:
            s = str(x).strip()
            if s and s not in out:
                out.append(s)
        return out


class ReportTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    template_id: int
    template_name: str | None = None
    cron_expr: str
    period_granularity: str = "month"
    period_offset: int = 0
    is_active: bool
    confirmer_email: str
    # 收件人邮箱列表：编辑任务回填用。只给 count 时前端拿不到已有值，改一次
    # 就会把原有收件人全部丢掉。邮箱不是密钥，与 confirmer_email 同等对待。
    recipient_emails: list[str] = []
    recipient_count: int = 0
    created_by: int
    created_at: datetime
    updated_at: datetime
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_skipped_at: datetime | None = None
    last_error: str | None = None
    # 该任务下"待确认"的积压数量（提醒用户去催确认）
    pending_confirmation: int = 0


class ReportTaskRunResult(BaseModel):
    """手动触发结果。"""

    report_id: int | None = None
    status: Literal["created", "skipped"]
    reason: str | None = None
