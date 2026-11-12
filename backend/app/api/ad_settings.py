"""AD / LDAP 对接的 API（2026-11-12）。

挂在「系统设置 → 运行时配置」下：

- ``GET  /settings/ad``       读 AD 配置（**不含密码**，只返回「已配置」标志）
- ``PUT  /settings/ad``       保存 AD 配置（密码留空 = 不修改）
- ``POST /settings/ad/test``  测试连接（用当前或临时配置试连 + 拉一条用户）
- ``POST /settings/ad/sync``  立即同步一次（admin）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.database import get_db
from app.services import ad_sync, ldap_client, runtime_config

router = APIRouter(prefix="/settings/ad", tags=["system-ad"])


class AdSettingsRead(BaseModel):
    """AD 配置概览（**没有密码字段**）。"""

    server: str = ""
    bind_dn: str = ""
    #: 服务账号密码是否已配置（接口永不返回密码本身）
    bind_password_set: bool = False
    base_dn: str = ""
    user_filter: str = "(&(objectClass=user)(sAMAccountType=805306368))"
    map_username: str = "sAMAccountName"
    map_display_name: str = "displayName"
    map_email: str = "mail"
    sync_enabled: bool = True
    sync_interval_minutes: int = 30
    timeout_seconds: int = 15
    #: 四项必填（server / bind_dn / password / base_dn）是否齐全
    configured: bool = False


class AdSettingsUpdate(BaseModel):
    """保存 AD 配置。密码留空 = 不修改。"""

    server: str = ""
    bind_dn: str = ""
    bind_password: str | None = None
    base_dn: str = ""
    user_filter: str = "(&(objectClass=user)(sAMAccountType=805306368))"
    map_username: str = "sAMAccountName"
    map_display_name: str = "displayName"
    map_email: str = "mail"
    sync_enabled: bool = True
    sync_interval_minutes: int = Field(default=30, ge=1, le=1440)
    timeout_seconds: int = Field(default=15, ge=1, le=120)


class AdTestResult(BaseModel):
    ok: bool
    message: str
    #: 测试时顺带拉到的第一条用户（证明映射字段可用）
    sample: dict[str, Any] | None = None


class AdSyncResult(BaseModel):
    total: int = 0
    created: int = 0
    updated: int = 0
    disabled: int = 0
    reactivated: int = 0
    vanished: int = 0
    skipped_no_email: int = 0
    errors: list[str] = []


def _read() -> AdSettingsRead:
    s = runtime_config.effective_settings()
    return AdSettingsRead(
        server=s.ad_server,
        bind_dn=s.ad_bind_dn,
        bind_password_set=bool(s.ad_bind_password),
        base_dn=s.ad_base_dn,
        user_filter=s.ad_user_filter,
        map_username=s.ad_map_username,
        map_display_name=s.ad_map_display_name,
        map_email=s.ad_map_email,
        sync_enabled=s.ad_sync_enabled,
        sync_interval_minutes=s.ad_sync_interval_minutes,
        timeout_seconds=s.ad_timeout_seconds,
        configured=bool(
            s.ad_server and s.ad_bind_dn and s.ad_bind_password and s.ad_base_dn
        ),
    )


@router.get("", response_model=AdSettingsRead)
async def get_ad_settings(
    _: Any = Depends(require_admin),
) -> AdSettingsRead:
    """读 AD 配置（不含密码）。"""
    return _read()


@router.put("", response_model=AdSettingsRead)
async def update_ad_settings(
    payload: AdSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _: Any = Depends(require_admin),
) -> AdSettingsRead:
    """保存 AD 配置。``bind_password`` 留空 = 不修改（不覆盖已存密码）。"""
    values: dict[str, Any] = {
        "ad_server": payload.server.strip(),
        "ad_bind_dn": payload.bind_dn.strip(),
        "ad_base_dn": payload.base_dn.strip(),
        "ad_user_filter": payload.user_filter.strip() or "(objectClass=user)",
        "ad_map_username": payload.map_username.strip() or "sAMAccountName",
        "ad_map_display_name": payload.map_display_name.strip() or "displayName",
        "ad_map_email": payload.map_email.strip() or "mail",
        "ad_sync_enabled": "true" if payload.sync_enabled else "false",
        "ad_sync_interval_minutes": str(payload.sync_interval_minutes),
        "ad_timeout_seconds": str(payload.timeout_seconds),
    }
    if payload.bind_password is not None and payload.bind_password.strip():
        values["ad_bind_password"] = payload.bind_password
    await runtime_config.save(db, values)
    runtime_config.refresh(db)
    return _read()


@router.post("/test", response_model=AdTestResult)
async def test_ad_connection(
    _: Any = Depends(require_admin),
) -> AdTestResult:
    """测试 AD 连接：绑定服务账号 + 拉一条用户（顺带验证字段映射）。"""
    s = runtime_config.effective_settings()
    cfg = ad_sync.config_from_settings(s)
    if not cfg.configured:
        return AdTestResult(ok=False, message="AD 配置不完整，请先填写服务器/绑定账号/密码/根 DN")
    try:
        users = ldap_client.list_users(cfg, limit=5)
    except ldap_client.AdError as exc:
        return AdTestResult(ok=False, message=str(exc))
    sample = None
    if users:
        u = users[0]
        sample = {
            "username": u.username,
            "display_name": u.display_name,
            "email": u.email,
            "disabled": u.disabled,
        }
    return AdTestResult(
        ok=True,
        message=f"连接成功，检索到 {len(users)} 个用户（示例只取前 5 条）",
        sample=sample,
    )


@router.post("/sync", response_model=AdSyncResult)
async def sync_ad_users(
    db: AsyncSession = Depends(get_db),
    _: Any = Depends(require_admin),
) -> AdSyncResult:
    """立即同步一次 AD 用户（建档 / 更新 / 停用）。"""
    s = runtime_config.effective_settings()
    cfg = ad_sync.config_from_settings(s)
    if not cfg.configured:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "AD 配置不完整，无法同步"
        )
    result = await ad_sync.sync_users(db, cfg)
    return AdSyncResult(**result.as_dict())
