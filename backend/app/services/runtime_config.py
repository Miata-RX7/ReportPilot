"""运行时配置覆盖层：页面上保存的邮件 / AI 配置覆盖 `.env` 默认值。

职责边界：
- `.env`（get_settings()）是**默认值**来源，本模块只在其上做差量覆盖。
- 落库：表 app_settings（key/value）。密钥类字段（SECRET_KEYS）Fernet 加密后存 value，
  其余明文存。
- 读取：进程内缓存 `_cache`（明文），由启动、写入与 GET /settings 触发刷新，
  这样 mailer / ai_insight 这类不持有 DB 会话的调用方也能读到最新配置。
- API 层**绝不**返回密钥；页面只能看到"是否已配置"。
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models.app_setting import AppSetting
from app.services.crypto import decrypt, encrypt

logger = logging.getLogger(__name__)

# 允许页面覆盖的配置项；其余（存储目录 / JWT / 调度等）一律以 .env 为准
EDITABLE_KEYS: frozenset[str] = frozenset(
    {
        "smtp_host",
        "smtp_port",
        "smtp_username",
        "smtp_password",
        "smtp_from",
        "smtp_use_tls",
        "app_base_url",
        "ai_base_url",
        "ai_api_key",
        "ai_model",
        # AI 系统提示词两段（非密钥，明文存 app_settings；空串 = 回落内置常量）
        "ai_system_prompt",
        "ai_system_rules",
    }
)

# 密钥类：落库前加密，接口永不返回
SECRET_KEYS: frozenset[str] = frozenset({"smtp_password", "ai_api_key"})

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

_cache: dict[str, str] | None = None


def reset_cache() -> None:
    """清空进程内缓存（测试 / 需强制回退 .env 时用）。"""
    global _cache
    _cache = None


def overrides() -> dict[str, str]:
    """当前生效的覆盖项（明文）。未载入时返回空字典。"""
    return dict(_cache or {})


async def refresh(db: AsyncSession) -> None:
    """从 DB 重新载入覆盖项到进程内缓存。"""
    global _cache
    rows = (await db.execute(select(AppSetting))).scalars().all()
    data: dict[str, str] = {}
    for row in rows:
        if row.key not in EDITABLE_KEYS:
            continue
        if row.key in SECRET_KEYS:
            try:
                data[row.key] = decrypt(row.value)
            except ValueError:
                # 密钥轮换后旧密文解不开：忽略该项并回退 .env，不阻断启动
                logger.warning("配置项 %s 解密失败，已忽略（回退 .env）", row.key)
                continue
        else:
            data[row.key] = row.value
    _cache = data
    logger.info("运行时配置覆盖项已载入: %s", sorted(data) or "无")


async def save(db: AsyncSession, values: dict[str, str | None]) -> None:
    """写入覆盖项并刷新缓存。

    values 中值为 None 表示"不修改"——用于密码 / API Key：
    留空保存等于保持原值，而不是把密钥清空。
    """
    for key, raw in values.items():
        if raw is None or key not in EDITABLE_KEYS:
            continue
        text = raw if isinstance(raw, str) else str(raw)
        if key in SECRET_KEYS:
            if not text.strip():
                continue  # 留空 = 不修改
            stored = encrypt(text)
        else:
            stored = text
        row = await db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=stored))
        else:
            row.value = stored
    await db.commit()
    await refresh(db)


async def reset(db: AsyncSession, keys: list[str]) -> None:
    """删除覆盖项（真删行）并刷新缓存。

    与 `save(..., {"k": ""})` 的区别：后者只是把空串写进表里，那一行仍然是
    「覆盖项」—— 恢复内置默认时必须删行，否则「是否已自定义」判断会含糊。
    """
    removed = False
    for key in keys:
        if key not in EDITABLE_KEYS:
            continue
        row = await db.get(AppSetting, key)
        if row is not None:
            await db.delete(row)
            removed = True
    if not removed:
        return
    await db.commit()
    await refresh(db)


def effective_settings() -> Settings:
    """合并 DB 覆盖项后的配置对象（供发信 / AI 等运行路径使用）。"""
    base = get_settings()
    data = _cache
    if not data:
        return base
    updates: dict[str, object] = {}
    for key, raw in data.items():
        if key not in EDITABLE_KEYS:
            continue
        try:
            updates[key] = _coerce(getattr(base, key), raw)
        except (TypeError, ValueError):
            logger.warning("配置项 %s 的值非法，已忽略该项", key)
    return base.model_copy(update=updates)


def _coerce(current: object, raw: str) -> object:
    """按 `.env` 默认值的类型，把 DB 中的字符串还原成目标类型。"""
    if isinstance(current, bool):
        return raw.strip().lower() in _TRUE_VALUES
    if isinstance(current, int):
        return int(raw)
    return raw