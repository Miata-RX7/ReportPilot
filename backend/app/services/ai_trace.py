"""AI 调用留痕的写入口。

埋点位置是 `ai_insight.generate_summary` —— 全系统发起 AI 请求的唯一出口。
调用方只负责说明「这是谁触发的哪一次」(`AiCallContext`)，本模块负责脱敏与落库。

两条纪律：

1. **只写不读密钥**（AGENTS.md 红线 1 / 4）：落库前对 `base_url` 去掉 userinfo，
   对异常文本统一脱敏，保证表中不出现 api_key / Bearer / Authorization。
2. **永不干扰主流程**：落库用**独立 session**，任何异常只告警不上抛 ——
   留痕失败不能让摘要生成失败；独立 session 也意味着调用方事务回滚时，
   已经真实发生过的 AI 请求记录仍然保留（例如"重生成时渲染失败不写库"，
   但那次 AI 调用确实花掉了 token，应当留痕）。
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import and_, delete, select

from app.database import AsyncSessionLocal
from app.models.ai_call import AiCallLog

logger = logging.getLogger(__name__)

# 每个「留痕主体」保留的最近记录数：报告按 report_id，试跑按 template_id。
# 单份报告通常只有几个摘要槽位，50 条足以覆盖「多次调提示词后重生成」的历史。
KEEP_PER_TARGET = 50
# 异常文本落库前的截断长度（避免把整个 traceback 塞进表里）
MAX_ERROR_CHARS = 500

_SECRET_RE = re.compile(
    r"(?i)(bearer\s+\S+|authorization[:\s]\S*|sk-[A-Za-z0-9_\-]{6,}|api[_-]?key\S*)"
)


@dataclass(slots=True)
class AiCallContext:
    """这次请求是谁触发的（决定记录挂在谁身上）。"""

    kind: str = "generate"
    report_id: int | None = None
    template_id: int | None = None
    slot: str | None = None
    period: str | None = None
    actor_id: int | None = None


@dataclass(slots=True)
class AiCallRecord:
    """一次 AI 请求的完整快照（含可选的护栏重试）。"""

    model: str = ""
    base_url: str = ""
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    has_custom: bool = False
    messages: list[dict[str, Any]] = field(default_factory=list)
    response_text: str | None = None
    status: str = "success"
    error: str | None = None
    retried: bool = False
    retry_messages: list[dict[str, Any]] | None = None
    retry_response_text: str | None = None
    retry_error: str | None = None
    duration_ms: int | None = None


def redact(text: str) -> str:
    """抹掉文本里可能出现的凭据形态（Bearer / Authorization / sk-xxx / api_key）。"""
    return _SECRET_RE.sub("***", text or "")


def sanitize_base_url(url: str) -> str:
    """去掉 URL 里的 userinfo 与 fragment，只留「协议://主机:端口/路径?参数」。

    `https://user:pass@host/v1` 这种形态真实存在于兼容 OpenAI 的网关上，
    凭据不能落库。
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:  # 非法 URL / 非法端口：整体脱敏后原样保留
        return redact(raw)[:255]
    if not parts.hostname:
        return redact(raw)[:255]
    netloc = parts.hostname if port is None else f"{parts.hostname}:{port}"
    rebuilt = urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
    return redact(rebuilt)[:255]


def safe_error(exc: BaseException | str | None) -> str | None:
    """异常 -> 可落库的一行文本（脱敏 + 截断）。"""
    if exc is None:
        return None
    text = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    text = redact(text).strip()
    return text[:MAX_ERROR_CHARS] or None


async def save(context: AiCallContext | None, record: AiCallRecord) -> None:
    """落库一条 AI 调用记录（context 为 None 表示调用方不要留痕，直接跳过）。"""
    if context is None:
        return
    try:
        async with AsyncSessionLocal() as db:
            db.add(
                AiCallLog(
                    report_id=context.report_id,
                    template_id=context.template_id,
                    kind=context.kind,
                    slot=(context.slot or None),
                    period=(context.period or None),
                    actor_id=context.actor_id,
                    model=(record.model or "")[:128],
                    base_url=sanitize_base_url(record.base_url),
                    temperature=record.temperature,
                    max_tokens=record.max_tokens,
                    timeout_seconds=record.timeout_seconds,
                    has_custom=record.has_custom,
                    messages=record.messages or [],
                    response_text=record.response_text,
                    status=record.status,
                    error=safe_error(record.error),
                    retried=record.retried,
                    retry_messages=record.retry_messages,
                    retry_response_text=record.retry_response_text,
                    retry_error=safe_error(record.retry_error),
                    duration_ms=record.duration_ms,
                )
            )
            await db.commit()
            await _prune(db, context)
    except Exception as exc:  # noqa: BLE001 - 留痕失败绝不影响摘要生成
        logger.warning("AI 调用留痕写入失败（已忽略）: %s: %s", type(exc).__name__, exc)


async def _prune(db: Any, context: AiCallContext) -> None:
    """只保留最近 KEEP_PER_TARGET 条，避免提示词反复试跑把表撑大。"""
    if context.report_id is not None:
        cond = AiCallLog.report_id == context.report_id
    elif context.template_id is not None:
        # 试跑历史：只清理同模板下不挂报告的那些
        cond = and_(
            AiCallLog.template_id == context.template_id,
            AiCallLog.report_id.is_(None),
        )
    else:
        return
    stale = (
        await db.scalars(
            select(AiCallLog.id)
            .where(cond)
            .order_by(AiCallLog.id.desc())
            .offset(KEEP_PER_TARGET)
        )
    ).all()
    if stale:
        await db.execute(delete(AiCallLog).where(AiCallLog.id.in_(list(stale))))
        await db.commit()