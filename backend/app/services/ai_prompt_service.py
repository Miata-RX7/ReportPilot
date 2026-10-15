"""AI 系统提示词（两段）的当前值与版本历史。

职责边界：
- **当前生效值**存在 `app_settings`（复用运行时配置覆盖层，改完立即生效、无需重启）
- **历史**存在 `ai_prompt_versions`（append-only），本模块负责写历史、查历史、回滚

三类动作都写一条历史，且都遵循同一条铁律：
**回滚不改写历史** —— 回滚是"读取某个历史版本的文本 → 写一条新的当前版本"，
所以 `ai_prompt_versions` 永远是一份可信的账本（谁在何时把两段规则改成了什么）。

红线：这两段文本不是密钥，可回显、可落库明文；本模块不接触任何 token / api_key。
"""

import logging

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_prompt import AiPromptVersion
from app.models.user import User
from app.services import runtime_config
from app.services.runtime_config import effective_settings

logger = logging.getLogger(__name__)

# 历史列表默认条数（够翻好几轮，又不至于把响应撑大）
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def current_texts() -> tuple[str, str]:
    """当前生效的全局两段文本 -> ``(基础规则, 追加规则)``。

    空串表示"未自定义"（回落内置常量）。这里读的是**已合并覆盖层**后的结果，
    与 AI 请求实际用到的口径一致。
    """
    s = effective_settings()
    return s.ai_system_prompt or "", s.ai_system_rules or ""


def has_custom(base_prompt: str, system_rules: str) -> bool:
    return bool(base_prompt.strip() or system_rules.strip())


async def record_change(
    db: AsyncSession,
    *,
    actor: User | None,
    action: str,
    base_prompt: str,
    system_rules: str,
    replaced_from_id: int | None = None,
    note: str | None = None,
    previous_base_prompt: str | None = None,
    previous_system_rules: str | None = None,
) -> AiPromptVersion:
    """追加一条版本记录（不改写任何已有行）。"""
    row = AiPromptVersion(
        actor_id=actor.id if actor else None,
        actor_name=(actor.username if actor else "")[:64],
        action=action,
        base_prompt=base_prompt,
        system_rules=system_rules,
        replaced_from_id=replaced_from_id,
        previous_base_prompt=previous_base_prompt,
        previous_system_rules=previous_system_rules,
        note=note[:255] if note else None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "系统提示词变更已记录: action=%s actor=%s base=%d 字 rules=%d 字 from=%s",
        action,
        row.actor_name or "-",
        len(base_prompt),
        len(system_rules),
        replaced_from_id if replaced_from_id is not None else "-",
    )
    return row


async def save_prompt(
    db: AsyncSession,
    *,
    actor: User | None,
    base_prompt: str,
    system_rules: str,
    note: str | None = None,
) -> AiPromptVersion:
    """保存全局两段文本并记一条 save 历史。"""
    prev_base, prev_rules = current_texts()
    await runtime_config.save(
        db, {"ai_system_prompt": base_prompt, "ai_system_rules": system_rules}
    )
    return await record_change(
        db,
        actor=actor,
        action="save",
        base_prompt=base_prompt,
        system_rules=system_rules,
        note=note,
        previous_base_prompt=prev_base,
        previous_system_rules=prev_rules,
    )


async def reset_to_default(
    db: AsyncSession, *, actor: User | None
) -> AiPromptVersion:
    """恢复内置默认：**删掉**两段覆盖行（而不是写空串），记一条历史。"""
    prev_base, prev_rules = current_texts()
    await runtime_config.reset(db, ["ai_system_prompt", "ai_system_rules"])
    return await record_change(
        db,
        actor=actor,
        action="reset_default",
        base_prompt="",
        system_rules="",
        note="恢复内置默认",
        previous_base_prompt=prev_base,
        previous_system_rules=prev_rules,
    )


async def rollback_to(
    db: AsyncSession, *, actor: User | None, version_id: int
) -> AiPromptVersion:
    """回滚到指定历史版本：取那两段文本写回当前值，并**另记**一条 rollback。"""
    target = await db.get(AiPromptVersion, version_id)
    if target is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"版本 {version_id} 不存在，无法回滚"
        )
    prev_base, prev_rules = current_texts()
    await runtime_config.save(
        db,
        {
            "ai_system_prompt": target.base_prompt,
            "ai_system_rules": target.system_rules,
        },
    )
    return await record_change(
        db,
        actor=actor,
        action="rollback",
        base_prompt=target.base_prompt,
        system_rules=target.system_rules,
        replaced_from_id=target.id,
        note=f"回滚到版本 #{target.id}",
        previous_base_prompt=prev_base,
        previous_system_rules=prev_rules,
    )


async def list_versions(
    db: AsyncSession, *, limit: int = DEFAULT_LIMIT
) -> list[AiPromptVersion]:
    """按时间倒序返回历史（最新在前）。"""
    capped = max(1, min(limit, MAX_LIMIT))
    stmt = select(AiPromptVersion).order_by(desc(AiPromptVersion.id)).limit(capped)
    return list((await db.execute(stmt)).scalars().all())


async def get_version(db: AsyncSession, version_id: int) -> AiPromptVersion:
    row = await db.get(AiPromptVersion, version_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"版本 {version_id} 不存在")
    return row