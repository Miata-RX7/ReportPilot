"""密码哈希（argon2）与 JWT 签发/校验。

- argon2 参数按 OWASP 推荐（m=64MiB, t=3, p=4）
- JWT 固定 HS256，负载 {sub, username, role, exp}（不加 iat/nbf）
- JWT_SECRET 从环境变量读，缺失时 fail-fast（与 ENCRYPTION_KEY 同等对待）
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import (
    Argon2Error,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

from app.config import get_settings

logger = logging.getLogger(__name__)

_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

JWT_ALGORITHM = "HS256"
MIN_PASSWORD_LENGTH = 8


class TokenError(Exception):
    """token 无效（签名 / 格式 / sub 非法）。"""


class TokenExpiredError(TokenError):
    """token 已过期。"""


def hash_password(plain: str) -> str:
    if len(plain) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """校验失败（含哈希损坏）一律返回 False，不泄露失败原因。"""
    try:
        return _ph.verify(hashed, plain)
    except (
        VerifyMismatchError,
        VerificationError,
        InvalidHashError,
        Argon2Error,
    ):
        return False


def _jwt_secret() -> str:
    secret = get_settings().jwt_secret
    if not secret:
        raise RuntimeError("JWT_SECRET 未配置，请设置环境变量后重启")
    return secret


def create_token(
    user_id: int,
    username: str,
    role: str,
    expire_days: int | None = None,
) -> str:
    settings = get_settings()
    days = expire_days if expire_days is not None else settings.jwt_expire_days
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=days),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """校验签名与过期，返回 payload。

    TokenExpiredError：已过期；TokenError：其他一切无效情形
    （含伪造的非数字 sub，int() 还原失败归为此类）。
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token, _jwt_secret(), algorithms=[JWT_ALGORITHM]
        )
        int(payload["sub"])  # 还原校验，非数字 sub 视为伪造
        return payload
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError("token 已过期") from exc
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError) as exc:
        raise TokenError("token 无效") from exc
