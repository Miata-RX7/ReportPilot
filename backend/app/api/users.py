"""用户管理 API：新建/ 列表（可选含停用）/ 编辑 / 删除。

- `GET /api/users` 供下拉选择（默认只返回启用用户，保持既有调用方行为）
- `GET /api/users?include_inactive=true` 供系统设置的用户管理页（需管理员）
- 新建 / 编辑 / 删除均限管理员
- 删除前检查引用，避免破坏历史数据
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.database import get_db
from app.models.report import Report
from app.models.task import ReportTask
from app.models.template import WordTemplate
from app.models.user import User
from app.schemas.auth import UserRead
from app.services.security import hash_password

router = APIRouter()


class UserCreate(BaseModel):
    """新建用户。密码至少 8 位。"""

    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str = Field(default="", max_length=128)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="user", pattern="^(admin|user)$")
    is_active: bool = True


class UserUpdate(BaseModel):
    """编辑用户（只开放这几项；username / source 不可改）。"""

    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, pattern="^(admin|user)$")
    is_active: bool | None = None


@router.post("", response_model=UserRead, status_code=201)
async def create_user(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> User:
    """新建用户（管理员）。登录名与邮箱均需唯一。"""
    if await db.scalar(select(User.id).where(User.username == payload.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"登录名已存在：{payload.username}")
    if await db.scalar(select(User.id).where(User.email == payload.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"邮箱已被使用：{payload.email}")

    user = User(
        username=payload.username,
        email=payload.email,
        display_name=payload.display_name or payload.username,
        source="local",
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=payload.is_active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("", response_model=list[UserRead])
async def list_users(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
    include_inactive: Annotated[bool, Query(description="是否包含停用用户（需管理员）")] = False,
) -> list[User]:
    """列出用户。

    - 默认只返回启用用户（模板授权、任务确认人等下拉用）
    - include_inactive=true 返回全部，**仅管理员**可用
    """
    if include_inactive and current.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅管理员可查看停用用户")
    stmt = select(User).order_by(User.id)
    if not include_inactive:
        stmt = stmt.where(User.is_active.is_(True))
    return list((await db.scalars(stmt)).all())


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int,
    payload: UserUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> User:
    """编辑用户（显示名/邮箱/角色/启用状态）。"""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    data = payload.model_dump(exclude_unset=True)

    # 最后一个启用中的管理员不能被降级或停用，否则系统无人可管
    if user.role == "admin" and (
        data.get("role") == "user" or data.get("is_active") is False
    ):
        others = await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.is_active.is_(True), User.id != user_id)
        )
        if not others:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "系统至少需要保留一个启用状态的管理员"
            )

    new_email = data.get("email")
    if new_email and new_email != user.email:
        dup = await db.scalar(select(User.id).where(User.email == new_email))
        if dup is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, f"邮箱已被使用：{new_email}")

    for field, value in data.items():
        if value is not None:
            setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> None:
    """删除用户。

    有任何引用（模板属主/ 任务创建者 / 报告创建者 / 任务确认人）时**拒绝删除**
    并提示改用「停用」—— 历史数据必须保留可追溯性。
    """
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if user.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "不能删除自己")

    refs: list[str] = []
    if await db.scalar(select(WordTemplate.id).where(WordTemplate.owner_id == user_id)):
        refs.append("模板")
    if await db.scalar(select(ReportTask.id).where(ReportTask.created_by == user_id)):
        refs.append("报告任务")
    if await db.scalar(select(Report.id).where(Report.created_by == user_id)):
        refs.append("报告实例")
    if await db.scalar(
        select(ReportTask.id).where(ReportTask.confirmer_email == user.email)
    ):
        refs.append("任务确认人")
    if refs:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"该用户仍被引用（{chr(12289).join(refs)}），无法删除；请改为「停用」",
        )

    await db.delete(user)
    await db.commit()
