"""系统设置：运行时配置概览 + 页面可编辑的邮件 / AI 配置 / AI 系统提示词。

安全约定：
- **不返回任何密钥**：只暴露"是否已配置"与影响行为的公开参数。
- SMTP 密码 / AI API Key 保存后只写不读；再次编辑时留空（或不传）表示"不修改"，
  而不是把密钥清空。
- 写操作需管理员（require_admin）。
- `.env` 仍是默认值来源；页面保存的项落库（app_settings）并覆盖默认值。

AI 系统提示词（两段：基础规则 / 有范文才追加的规则）是**规则文本而非密钥**，
可回显、可明文落库；变更历史见 ai_prompt_versions（append-only）。
"""

import logging
from dataclasses import replace
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.database import get_db
from app.models.template import WordTemplate
from app.models.user import User
from app.schemas.template import InsightPreviewResult
from app.services import (
    ai_insight,
    ai_prompt_service,
    ai_trace,
    runtime_config,
    template_service,
)
from app.services.adapters.base import (
    AdapterAPIError,
    AdapterAuthError,
    AdapterConnectionError,
    AdapterPermissionError,
)
from app.services.ai_insight import InsightConfig
from app.services.period_utils import PERIOD_PATTERN
from app.services.preview_service import (
    PreviewError,
    collect_facts,
    compute_template_facts,
)
from app.services.runtime_config import effective_settings
from app.services.scheduler import scheduler_status

logger = logging.getLogger(__name__)

router = APIRouter()

# 列表里每段文本回显的字数（完整内容走详情端点，避免响应被撑大）
_PREVIEW_CHARS = 160


class SystemSettingsRead(BaseModel):
    """运行时配置概览（无密钥）。"""

    # 邮件
    smtp_host: str
    smtp_port: int
    smtp_from: str
    # 用户名非密钥，为支持页面编辑而返回
    smtp_username: str
    smtp_use_tls: bool
    smtp_ready: bool
    # 密码是否已配置（值本身绝不返回）
    smtp_secret_set: bool
    # 确认流程
    app_base_url: str
    confirm_token_ttl_hours: int
    reminder_interval_hours: int
    task_contact_email: str
    # 调度
    scheduler_enabled: bool
    scheduler_running: bool
    scheduler_jobs: list[str]
    report_timezone: str
    misfire_grace_hours: int
    # 其它集成
    onlyoffice_enabled: bool
    # AI
    ai_base_url: str
    ai_model: str
    ai_ready: bool
    # API Key 是否已配置（值本身绝不返回）
    ai_secret_set: bool
    # 两段系统提示词是否已被自定义（False = 用内置默认）
    ai_prompt_custom: bool


class AiPromptRead(BaseModel):
    """全局系统提示词的当前值 + 内置默认（供页面编辑与「查看内置默认」）。

    两段文本**不是密钥**，可回显；内置默认实时取`ai_insight` 的常量，
    不在前端硬编码（规则文案会随代码演进而变化）。
    """

    base_prompt: str
    system_rules: str
    has_custom: bool
    default_base_prompt: str
    default_system_rules: str


class AiPromptUpdate(BaseModel):
    """保存载荷。空串 = 该段回落内置默认（不是"清空为无规则"）。"""

    base_prompt: str = Field(
        default="", max_length=ai_insight.MAX_SYSTEM_PROMPT_CHARS
    )
    system_rules: str = Field(
        default="", max_length=ai_insight.MAX_SYSTEM_RULES_CHARS
    )
    note: str | None = Field(default=None, max_length=255)


class AiPromptVersionRead(BaseModel):
    """单个版本的完整内容（详情/ 回滚前确认用）。"""

    id: int
    action: str
    actor_name: str
    base_prompt: str
    system_rules: str
    previous_base_prompt: str | None
    previous_system_rules: str | None
    replaced_from_id: int | None
    note: str | None
    created_at: datetime


class AiPromptVersionSummary(BaseModel):
    """列表项：不含全文，只给字数与前若干字预览。"""

    id: int
    action: str
    actor_name: str
    base_len: int
    rules_len: int
    base_preview: str
    rules_preview: str
    replaced_from_id: int | None
    note: str | None
    created_at: datetime


class AiPromptRollback(BaseModel):
    version_id: int


class AiPromptPreviewRequest(BaseModel):
    """用页面上的两段文本试跑一次（不落库）。

    两段文本都允许留空：留空表示"这一段沿用全局/内置默认"，所以**不点保存也能
    试跑当前生效的规则**。写作要求 / 参考范文取该模板绑定的已存配置（试的确实是
    真实上下文）。
    """

    template_id: int
    placeholder: str = Field(min_length=1, max_length=128)
    period: str = Field(pattern=PERIOD_PATTERN)
    base_prompt: str = Field(
        default="", max_length=ai_insight.MAX_SYSTEM_PROMPT_CHARS
    )
    system_rules: str = Field(
        default="", max_length=ai_insight.MAX_SYSTEM_RULES_CHARS
    )


class MailSettingsUpdate(BaseModel):
    """邮件集成保存载荷。密码留空或不传 = 保持原密码不变。"""

    smtp_host: str = ""
    smtp_port: int = Field(default=25, ge=1, le=65535)
    smtp_username: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = False
    app_base_url: str = ""
    smtp_password: str | None = None


class AiSettingsUpdate(BaseModel):
    """AI 集成保存载荷。API Key 留空或不传 = 保持原 Key 不变。"""

    ai_base_url: str = ""
    ai_model: str = ""
    ai_api_key: str | None = None


def _secret_or_none(value: str | None) -> str | None:
    """空串 / 未提供 -> None（表示不修改）；否则原样返回。"""
    if value is None or not value.strip():
        return None
    return value


def _preview(text: str) -> str:
    """列表用的文本预览：单行化 + 截断。"""
    flat = " ".join(text.split())
    if len(flat) <= _PREVIEW_CHARS:
        return flat
    return flat[:_PREVIEW_CHARS] + "…"


def _read_model() -> SystemSettingsRead:
    """按当前生效配置组装概览响应（不含任何密钥）。"""
    s = effective_settings()
    status = scheduler_status()
    return SystemSettingsRead(
        smtp_host=s.smtp_host,
        smtp_port=s.smtp_port,
        smtp_from=s.smtp_from,
        smtp_username=s.smtp_username,
        smtp_use_tls=s.smtp_use_tls,
        # host + 发件人 + 密码齐全才可发信
        smtp_ready=bool(s.smtp_host and s.smtp_from and s.smtp_password),
        smtp_secret_set=bool(s.smtp_password),
        app_base_url=s.app_base_url,
        confirm_token_ttl_hours=s.confirm_token_ttl_hours,
        reminder_interval_hours=s.reminder_interval_hours,
        task_contact_email=s.task_contact_email,
        scheduler_enabled=s.scheduler_enabled,
        scheduler_running=bool(status.get("running")),
        scheduler_jobs=list(status.get("jobs") or []),
        report_timezone=s.report_timezone,
        misfire_grace_hours=s.misfire_grace_hours,
        onlyoffice_enabled=s.onlyoffice_enabled,
        ai_base_url=s.ai_base_url,
        ai_model=s.ai_model,
        ai_ready=bool(s.ai_api_key),
        ai_secret_set=bool(s.ai_api_key),
        ai_prompt_custom=ai_prompt_service.has_custom(
            s.ai_system_prompt, s.ai_system_rules
        ),
    )


def _prompt_read() -> AiPromptRead:
    """组装「当前值 + 内置默认」响应（内置默认实时取常量）。"""
    base, rules = ai_prompt_service.current_texts()
    return AiPromptRead(
        base_prompt=base,
        system_rules=rules,
        has_custom=ai_prompt_service.has_custom(base, rules),
        default_base_prompt=ai_insight.SYSTEM_PROMPT,
        default_system_rules=ai_insight.SYSTEM_PROMPT_USER_INPUT,
    )


@router.get("/settings", response_model=SystemSettingsRead)
async def read_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> SystemSettingsRead:
    """返回运行时配置概览（登录即可读，不含任何密钥）。"""
    # 顺带刷新覆盖缓存：多副本或启动载入失败时，打开设置页即可自愈
    try:
        await runtime_config.refresh(db)
    except Exception as exc:  # 缓存刷新失败不应阻断只读概览
        logger.warning("运行时配置缓存刷新失败: %s", exc)
    return _read_model()


@router.put("/settings/mail", response_model=SystemSettingsRead)
async def update_mail_settings(
    payload: MailSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> SystemSettingsRead:
    """保存邮件集成配置（管理员）。密码留空 = 不修改原密码。"""
    await runtime_config.save(
        db,
        {
            "smtp_host": payload.smtp_host.strip(),
            "smtp_port": str(payload.smtp_port),
            "smtp_username": payload.smtp_username.strip(),
            "smtp_from": payload.smtp_from.strip(),
            "smtp_use_tls": "true" if payload.smtp_use_tls else "false",
            "app_base_url": payload.app_base_url.strip(),
            "smtp_password": _secret_or_none(payload.smtp_password),
        },
    )
    return _read_model()


@router.put("/settings/ai", response_model=SystemSettingsRead)
async def update_ai_settings(
    payload: AiSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> SystemSettingsRead:
    """保存 AI 集成配置（管理员）。API Key 留空 = 不修改原 Key。"""
    await runtime_config.save(
        db,
        {
            "ai_base_url": payload.ai_base_url.strip(),
            "ai_model": payload.ai_model.strip(),
            "ai_api_key": _secret_or_none(payload.ai_api_key),
        },
    )
    return _read_model()


# ---------------------------------------------------------------------------
# AI 系统提示词（两段规则文本）：读取 / 保存 / 恢复默认 / 历史 / 回滚
# ---------------------------------------------------------------------------


@router.get("/settings/ai-prompt", response_model=AiPromptRead)
async def read_ai_prompt(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> AiPromptRead:
    """读取当前两段文本 + 内置默认（登录即可读；文本不是密钥）。"""
    try:
        await runtime_config.refresh(db)
    except Exception as exc:  # 缓存刷新失败不应阻断只读
        logger.warning("运行时配置缓存刷新失败: %s", exc)
    return _prompt_read()


@router.put("/settings/ai-prompt", response_model=AiPromptRead)
async def update_ai_prompt(
    payload: AiPromptUpdate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
) -> AiPromptRead:
    """保存两段文本（管理员）。立即生效，无需重启；空串 = 该段回落内置默认。"""
    await ai_prompt_service.save_prompt(
        db,
        actor=actor,
        base_prompt=payload.base_prompt.strip(),
        system_rules=payload.system_rules.strip(),
        note=payload.note,
    )
    return _prompt_read()


@router.post("/settings/ai-prompt/reset", response_model=AiPromptRead)
async def reset_ai_prompt(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
) -> AiPromptRead:
    """恢复内置默认（管理员）：清空两段覆盖值，历史保留。"""
    await ai_prompt_service.reset_to_default(db, actor=actor)
    return _prompt_read()


@router.get("/settings/ai-prompt/versions", response_model=list[AiPromptVersionSummary])
async def list_ai_prompt_versions(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
    limit: int = Query(default=ai_prompt_service.DEFAULT_LIMIT, ge=1, le=200),
) -> list[AiPromptVersionSummary]:
    """版本历史（最新在前）。列表不含全文，点开看详情端点。"""
    rows = await ai_prompt_service.list_versions(db, limit=limit)
    return [
        AiPromptVersionSummary(
            id=r.id,
            action=r.action,
            actor_name=r.actor_name,
            base_len=len(r.base_prompt),
            rules_len=len(r.system_rules),
            base_preview=_preview(r.base_prompt),
            rules_preview=_preview(r.system_rules),
            replaced_from_id=r.replaced_from_id,
            note=r.note,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get(
    "/settings/ai-prompt/versions/{version_id}", response_model=AiPromptVersionRead
)
async def read_ai_prompt_version(
    version_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> AiPromptVersionRead:
    """单个版本的完整两段文本（回滚前确认用）。"""
    row = await ai_prompt_service.get_version(db, version_id)
    return AiPromptVersionRead(
        id=row.id,
        action=row.action,
        actor_name=row.actor_name,
        base_prompt=row.base_prompt,
        system_rules=row.system_rules,
        previous_base_prompt=row.previous_base_prompt,
        previous_system_rules=row.previous_system_rules,
        replaced_from_id=row.replaced_from_id,
        note=row.note,
        created_at=row.created_at,
    )


@router.post("/settings/ai-prompt/rollback", response_model=AiPromptRead)
async def rollback_ai_prompt(
    payload: AiPromptRollback,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
) -> AiPromptRead:
    """回滚到指定版本（管理员）：写回该版本文本，并另记一条 rollback 历史。"""
    await ai_prompt_service.rollback_to(
        db, actor=actor, version_id=payload.version_id
    )
    return _prompt_read()


@router.post("/settings/ai-prompt/preview", response_model=InsightPreviewResult)
async def preview_ai_prompt(
    payload: AiPromptPreviewRequest,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
) -> InsightPreviewResult:
    """用页面上的两段规则试跑一次（管理员）：真实取数 + 真实调用，不落库。

    取数与异常映射完全复用模板页那条路径（compute_template_facts），只是把两段
    system 文本换成页面上的内容——所以试跑看到的就是"保存后会发生什么"。
    试跑同样写 AI 留痕（kind=preview），可在「查看本次 AI 请求」里核对真正发出去的
    messages 原文。**试跑不会保存**，要生效还需点保存。
    """
    tpl = await db.get(WordTemplate, payload.template_id)
    if tpl is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"模板 {payload.template_id} 不存在"
        )
    if not tpl.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "模板已停用")
    version = await template_service.get_current_version(db, tpl)
    if version is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "模板无可用版本")

    try:
        bindings, facts_by_ph, _metrics = await compute_template_facts(
            db, version, payload.period
        )
    except PreviewError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
        ) from None
    except AdapterConnectionError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"数据源不可达: {exc}"
        ) from None
    except AdapterAuthError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"数据源认证失败: {exc}"
        ) from None
    except AdapterPermissionError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"数据源权限不足: {exc}"
        ) from None
    except AdapterAPIError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"数据源接口错误: {exc}"
        ) from None

    facts_list = collect_facts(facts_by_ph)
    if not facts_list:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "当前版本没有任何已绑定的指标，无法生成摘要",
        )

    # 写作要求 / 参考范文 / 字数 / 温度：用该绑定**已存**的配置（试真实上下文）
    slot_key = payload.placeholder
    stored: dict | None = None
    for b in bindings:
        if b.binding_type == "insight" and (
            b.placeholder == payload.placeholder or b.binding_key == payload.placeholder
        ):
            slot_key = b.placeholder
            stored = b.ai_config_json
            break
    else:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"模板里没有占位符「{payload.placeholder}」的 AI 摘要绑定",
        )

    config = InsightConfig.from_mapping(stored)
    # 页面上的两段文本覆盖（留空 = 该段沿用全局/内置默认）
    preview_config = replace(
        config,
        system_prompt=payload.base_prompt.strip(),
        system_rules=payload.system_rules.strip(),
    )
    result = await ai_insight.generate_summary(
        facts_list,
        payload.period,
        preview_config,
        trace=ai_trace.AiCallContext(
            kind="preview",
            template_id=tpl.id,
            slot=slot_key,
            period=payload.period,
            actor_id=actor.id,
        ),
    )
    logger.info(
        "系统提示词试跑 template_id=%d slot=%s metrics=%d degraded=%s"
        " base=%d 字 rules=%d 字",
        tpl.id,
        slot_key,
        len(facts_list),
        result["degraded"],
        len(payload.base_prompt.strip()),
        len(payload.system_rules.strip()),
    )
    return InsightPreviewResult(
        text=result["text"],
        unverified_numbers=result["unverified_numbers"],
        sample_leak=result["sample_leak"],
        degraded=result["degraded"],
        retried=result["retried"],
        model=result["model"],
        metric_count=len(facts_list),
    )