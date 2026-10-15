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
from app.services.security import create_token, hash_password, verify_password

logger = logging.getLogger(__name__)

router = APIRouter()

# 固定延时防暴力破解（无论成功失败；Phase 2 换限流）
LOGIN_DELAY_SECONDS = 0.5

# 用户不存在时也执行一次真实校验，消除计时侧信道
_DUMMY_HASH = hash_password("dummy-password-for-timing")


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    client_ip = request.client.host if request.client else "-"
    user = await db.scalar(select(User).where(User.username == payload.username))
    hashed = user.password_hash if user and user.password_hash else _DUMMY_HASH
    ok = (
        verify_password(payload.password, hashed)
        and user is not None
        and user.is_active
    )
    await asyncio.sleep(LOGIN_DELAY_SECONDS)
    if not ok:
        # 日志与响应均不区分"用户不存在"与"密码错误"；绝不记录密码
        logger.warning("登录失败 username=%s ip=%s", payload.username, client_ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    assert user is not None
    logger.info("登录成功 username=%s ip=%s", payload.username, client_ip)
    return TokenResponse(
        access_token=create_token(user.id, user.username, user.role),
        user=UserRead.model_validate(user),
    )


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
