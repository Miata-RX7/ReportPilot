"""AI 调用留痕 schema（对应 ai_call_logs 表）。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AiCallItem(BaseModel):
    """列表项：只给「这次调用的轮廓」，正文点开再看（一份 messages 可能几 KB）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    slot: str | None
    period: str | None
    model: str
    status: str
    retried: bool
    # 是否带自定义提示词 / 范文
    has_custom: bool
    duration_ms: int | None
    created_at: datetime


class AiCallDetail(AiCallItem):
    """详情：完整请求快照（不含 api_key / Authorization）。"""

    report_id: int | None
    template_id: int | None
    actor_id: int | None
    # 已去 userinfo 的 OpenAI 兼容地址
    base_url: str
    temperature: float | None
    max_tokens: int | None
    timeout_seconds: float | None
    # 真正发出去的 messages（system 红线 + user 段，含指标 JSON）
    messages: list[dict[str, Any]] = Field(default_factory=list)
    response_text: str | None
    error: str | None
    # 护栏重试：命中「照抄范文数字 / 整句复述」后的第二次请求
    retry_messages: list[dict[str, Any]] | None = None
    retry_response_text: str | None = None
    retry_error: str | None = None


class AiCallList(BaseModel):
    total: int
    items: list[AiCallItem]