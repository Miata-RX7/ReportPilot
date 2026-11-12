"""认证 API：登录签发 JWT / 当前用户信息。"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse, UserRead
from app.services import ad_sync, ldap_client
from app.services.ldap_client import AdAuthError, AdError
from app.services.security import create_token, hash_password, verify_password

logger = logging.getLogger(__name__)

router = APIRouter()

# 固定延时防暴力破解（无论成功失败；Phase 2 换限流）
LOGIN_DELAY_SECONDS = 0.5

# 用户不存在时也执行一次真实校验，消除计时侧信道
_DUMMY_HASH = hash_password("dummy-password-for-timing")


async def _authenticate_via_ad(
    db: AsyncSession, username: str, password: str
) -> User | None:
    """走 AD 认证。成功返回用户（必要时自动建档）；失败返回 None。

    AD 不可用（网络/配置/服务账号问题）时**不阻断登录** —— 返回 None，
    统一按「用户名或密码错误」处理，避免把内部故障暴露给登录页。
    """
    settings = ad_sync_settings()
    if settings is None:
        return None
    cfg = ad_sync.config_from_settings(settings)
    if not cfg.configured:
        return None
    try:
        ad_user = ldap_client.authenticate(cfg, username, password)
    except AdAuthError:
        return None
    except AdError as exc:
        logger.error("AD 认证失败（AD 不可用）：%s", exc)
        return None
    try:
        user, created = await ad_sync.upsert_on_login(db, ad_user)
    except Exception:
        logger.exception("AD 用户建档失败 username=%s", username)
        return None
    if created:
        logger.info("AD 用户首次登录已建档 username=%s", username)
    return user


def ad_sync_settings():
    """取运行时配置（DB 覆盖 .env）。失败返回 None。"""
    from app.services import runtime_config

    try:
        return runtime_config.effective_settings()
    except Exception:
        logger.exception("读取运行时配置失败")
        return None


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """登录。

    **本地账号优先**（admin / ops，密码哈希在本地）；本地对不上再走 **AD**。
    AD 用户在本地不存在时，首次登录自动建档（同步任务没跑到也能用）。
    """
    client_ip = request.client.host if request.client else "-"
    user = await db.scalar(select(User).where(User.username == payload.username))

    # ---- 本地账号：密码哈希在本地 ----
    if user is not None and user.source != "ad" and user.password_hash:
        ok = verify_password(payload.password, user.password_hash) and user.is_active
        await asyncio.sleep(LOGIN_DELAY_SECONDS)
        if not ok:
            logger.warning("登录失败 username=%s ip=%s", payload.username, client_ip)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
        logger.info("登录成功 username=%s ip=%s (local)", payload.username, client_ip)
        return TokenResponse(
            access_token=create_token(user.id, user.username, user.role),
            user=UserRead.model_validate(user),
        )

    # ---- AD 账号：密码由 AD 校验 ----
    ad_user = await _authenticate_via_ad(db, payload.username, payload.password)
    await asyncio.sleep(LOGIN_DELAY_SECONDS)
    if ad_user is None:
        logger.warning("登录失败 username=%s ip=%s", payload.username, client_ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    if not ad_user.is_active:
        logger.warning("登录被拒（账号已停用）username=%s", payload.username)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已停用，请联系管理员")
    logger.info("登录成功 username=%s ip=%s (ad)", payload.username, client_ip)
    return TokenResponse(
        access_token=create_token(ad_user.id, ad_user.username, ad_user.role),
        user=UserRead.model_validate(ad_user),
    )


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
