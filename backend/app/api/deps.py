"""API 层共享辅助函数与认证依赖。

401/403 边界：
- 无 token / token 无效或过期 / 用户不存在或停用 -> 401（get_current_user 抛）
- 有效 token 但 role != "admin" -> 403（require_admin 抛）
"""

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.datasource import DataSource
from app.models.user import User
from app.services.adapters import build_adapter
from app.services.adapters.base import DataSourceAdapter
from app.services.crypto import decrypt
from app.services.security import TokenError, TokenExpiredError, decode_token


async def load_adapter_and_name(
    db: AsyncSession, datasource_id: int
) -> tuple[DataSourceAdapter, str]:
    """加载数据源 → 校验 is_active → 解密 secret → 构建 Adapter。

    返回 (adapter, source_name)。供 compute / preview / 报告生成复用。
    """
    ds = await db.get(DataSource, datasource_id)
    if ds is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "关联的数据源不存在")
    if not ds.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "关联的数据源已停用")
    if not ds.secret_encrypted:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "数据源未配置密钥")
    try:
        secret = decrypt(ds.secret_encrypted)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "数据源密钥解密失败，请重新配置"
        ) from None
    try:
        adapter = build_adapter(ds.type, ds.config_json, secret)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
        ) from None
    return adapter, ds.name


async def resolve_user(db: AsyncSession, authorization: str | None) -> User:
    """从 Authorization: Bearer <token> 解析并校验用户（供非依赖场景复用）。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录或 token 缺失")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except TokenExpiredError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token 已过期") from None
    except (TokenError, ValueError, KeyError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token 无效") from None
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在或已停用")
    return user


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """从 Authorization: Bearer <token> 解析并校验当前用户。"""
    return await resolve_user(db, authorization)


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """要求管理员角色（数据源/指标/模板的写操作）。"""
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return user
