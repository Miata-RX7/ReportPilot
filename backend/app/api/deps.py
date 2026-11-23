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
        # cache_ns 传数据源 id：浏览类缓存（主机组/主机/监控项的清单与名称）
        # 必须按数据源隔离，同一台 Zabbix 用不同 token 时可见范围不同
        adapter = build_adapter(ds.type, ds.config_json, secret, cache_ns=ds.id)
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
    """从 Authorization: Bearer <token> 解析并校验当前用户。

    顺带**挂载角色组共享范围**（2026-11-23）：同角色组成员的指标/任务对
    当前用户**只读可见**。在这里统一挂一次，所有入口自动生效；
    未挂载时判定函数保守（只看自己的），不会误放权。
    """
    user = await resolve_user(db, authorization)
    from app.services.ownership import attach_share_scope

    await attach_share_scope(db, user)
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """要求管理员权限（数据源/指标/模板的写操作）。

    术语说明（2026-11-23）：``users.role`` 这一列在页面上叫「用户权限」
    （两档：admin / user）。不要与新增的「角色组」（共享圈）混淆。
    """
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return user
