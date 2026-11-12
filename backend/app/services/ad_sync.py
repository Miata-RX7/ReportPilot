"""AD 用户同步（2026-11-12）。

把 AD 里的用户同步到平台 ``users`` 表：

- **新建**：AD 里有、本地没有 -> 建档（``source='ad'``、``role='user'``、
  ``password_hash=NULL``、``is_active`` 按 AD 状态）
- **更新**：显示名 / 邮箱跟着 AD 变
- **停用**：AD 里被禁用（``userAccountControl`` bit 0x2）-> 本地 ``is_active=False``
- **消失**：AD 里删了的人 -> 本地停用（**不删除**，保留历史归属与审计）

## 边界（刻意不做）

- **不碰本地账号**：``source='local'`` 的（admin / ops）完全不参与同步
- **不改角色**：AD 同步的用户一律 ``role='user'``，管理员权限只在本地账号上
- **不删用户**：只停用 —— 报告的 created_by、模板的 owner_id 都指向 user.id，
  删了会丢历史归属
- **不写密码**：AD 用户本地不存密码

## 频率与开关

由 ``ad_sync_enabled`` / ``ad_sync_interval_minutes`` 控制（可在系统设置里改），
调度器按间隔周期调用 :func:`sync_users`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services import ldap_client
from app.services.ldap_client import AdAuthError, AdConfig, AdError, AdUser

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """同步结果统计。"""

    total: int = 0
    created: int = 0
    updated: int = 0
    disabled: int = 0
    reactivated: int = 0
    vanished: int = 0
    skipped_no_email: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "created": self.created,
            "updated": self.updated,
            "disabled": self.disabled,
            "reactivated": self.reactivated,
            "vanished": self.vanished,
            "skipped_no_email": self.skipped_no_email,
            "errors": self.errors[:20],
        }


def config_from_settings(settings: Any) -> AdConfig:
    """从运行时配置构造 AD 连接配置。"""
    return AdConfig(
        server=str(getattr(settings, "ad_server", "") or "").strip(),
        bind_dn=str(getattr(settings, "ad_bind_dn", "") or "").strip(),
        bind_password=str(getattr(settings, "ad_bind_password", "") or ""),
        base_dn=str(getattr(settings, "ad_base_dn", "") or "").strip(),
        user_filter=str(getattr(settings, "ad_user_filter", "") or "(objectClass=user)"),
        map_username=str(getattr(settings, "ad_map_username", "") or "sAMAccountName"),
        map_display_name=str(
            getattr(settings, "ad_map_display_name", "") or "displayName"
        ),
        map_email=str(getattr(settings, "ad_map_email", "") or "mail"),
        timeout=int(getattr(settings, "ad_timeout_seconds", 15) or 15),
    )


async def sync_users(db: AsyncSession, cfg: AdConfig) -> SyncResult:
    """把 AD 用户同步进本地库。"""
    result = SyncResult()
    try:
        ad_users = ldap_client.list_users(cfg)
    except AdError as exc:
        result.errors.append(str(exc))
        logger.error("AD 拉取用户失败：%s", exc)
        return result

    result.total = len(ad_users)
    ad_by_name = {u.username: u for u in ad_users if u.username}
    logger.info("AD 同步：拉到 %d 个用户", len(ad_by_name))

    local_rows = list((await db.scalars(select(User))).all())
    local_by_name = {u.username: u for u in local_rows}

    # ---- AD 里有的人：新建 / 更新 / 停用启用 ----
    for name, au in ad_by_name.items():
        user = local_by_name.get(name)
        if user is not None and user.source != "ad":
            # 本地账号（admin / ops）不受 AD 同步影响
            continue
        if user is None:
            if not au.email:
                result.skipped_no_email += 1
                continue
            # 邮箱唯一约束：AD 里有人跟本地账号撞邮箱 -> 跳过并记录
            existing = await db.scalar(select(User.id).where(User.email == au.email))
            if existing is not None:
                result.errors.append(f"{name}: 邮箱 {au.email} 已被其它账号占用")
                result.skipped_no_email += 1
                continue
            user = User(
                username=name,
                email=au.email,
                display_name=au.display_name or name,
                source="ad",
                password_hash=None,
                role="user",
                is_active=not au.disabled,
            )
            db.add(user)
            local_by_name[name] = user
            result.created += 1
            continue

        # 已存在：更新显示名 / 邮箱 + 同步状态
        changed = False
        if au.display_name and user.display_name != au.display_name:
            user.display_name = au.display_name
            changed = True
        if au.email and user.email != au.email:
            clash = await db.scalar(
                select(User.id).where(User.email == au.email, User.id != user.id)
            )
            if clash is None:
                user.email = au.email
                changed = True
            else:
                result.errors.append(f"{name}: 新邮箱 {au.email} 已被占用")
        if au.disabled and user.is_active:
            user.is_active = False
            result.disabled += 1
            changed = True
        elif not au.disabled and not user.is_active:
            user.is_active = True
            result.reactivated += 1
            changed = True
        if changed:
            result.updated += 1

    # ---- AD 里消失的 AD 用户 -> 停用（不删除）----
    for name, user in local_by_name.items():
        if user.source != "ad" or name in ad_by_name:
            continue
        if user.is_active:
            user.is_active = False
            result.vanished += 1

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        result.errors.append(f"提交失败：{exc}")
        logger.exception("AD 同步提交失败")
        return result

    logger.info(
        "AD 同步完成：新建 %d 更新 %d 停用 %d 启用 %d 消失 %d 跳过 %d",
        result.created,
        result.updated,
        result.disabled,
        result.reactivated,
        result.vanished,
        result.skipped_no_email,
    )
    return result


async def upsert_on_login(db: AsyncSession, au: AdUser) -> tuple[User, bool]:
    """登录时按 AD 信息建档 / 更新本地用户。返回 (用户, 是否新建)。

    同步任务还没跑到、但用户已经登录的情况靠这里兜底。
    """
    user = await db.scalar(select(User).where(User.username == au.username))
    if user is None:
        email = au.email or f"{au.username}@ad.local"
        clash = await db.scalar(select(User.id).where(User.email == email))
        if clash is not None:
            # 邮箱被占用 -> 用一个带后缀的占位邮箱，保证能登录
            email = f"{au.username}+ad@ad.local"
        user = User(
            username=au.username,
            email=email,
            display_name=au.display_name or au.username,
            source="ad",
            password_hash=None,
            role="user",
            is_active=not au.disabled,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user, True

    if user.source != "ad":
        # **本地账号撞名**：本地账号（admin / ops）只能用**本地密码**登录。
        # AD 里恰好有个同名账号（现场 AD 就有个 admin）时，
        # 如果这里返回本地用户，AD 密码就能换来本地 admin 的权限 —— 越权。
        # 所以：拒绝，本地账号不参与 AD 认证。
        raise AdAuthError("该账号为本地账号，请使用本地密码登录")

    changed = False
    if au.display_name and user.display_name != au.display_name:
        user.display_name = au.display_name
        changed = True
    if au.email and user.email != au.email:
        clash = await db.scalar(
            select(User.id).where(User.email == au.email, User.id != user.id)
        )
        if clash is None:
            user.email = au.email
            changed = True
    if au.disabled and user.is_active:
        user.is_active = False
        changed = True
    elif not au.disabled and not user.is_active:
        user.is_active = True
        changed = True
    if changed:
        await db.commit()
        await db.refresh(user)
    return user, False
