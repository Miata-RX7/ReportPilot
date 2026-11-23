"""角色组（共享圈）接口（2026-11-23）。

「角色组」= 一组人互相可见对方的指标/报告任务（**只读**）。
与「用户权限」（``users.role`` 的 admin/user 两档）是两回事。

可见性：
- **admin**：全部角色
- **普通用户**：只返回**自己加入的**角色（看不到角色目录全貌）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.database import get_db
from app.models.role import Role, RoleMember
from app.models.user import User

router = APIRouter()


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = ""


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = None


class RoleMembersAdd(BaseModel):
    user_ids: list[int] = Field(default_factory=list)


class RoleRead(BaseModel):
    id: int
    name: str
    description: str
    created_by: int | None = None
    created_by_name: str | None = None
    member_count: int = 0
    created_at: str


class RoleMemberRead(BaseModel):
    user_id: int
    username: str
    display_name: str
    email: str


def _to_read(
    role: Role, member_count: int, owner_name: str | None
) -> RoleRead:
    return RoleRead(
        id=role.id,
        name=role.name,
        description=role.description or "",
        created_by=role.created_by,
        created_by_name=owner_name,
        member_count=member_count,
        created_at=role.created_at.isoformat() if role.created_at else "",
    )


async def _member_counts(db: AsyncSession) -> dict[int, int]:
    rows = (
        await db.execute(
            select(RoleMember.role_id, func.count(RoleMember.user_id)).group_by(
                RoleMember.role_id
            )
        )
    ).all()
    return {int(rid): int(cnt) for rid, cnt in rows}


async def _owner_names(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(User.id, User.display_name, User.username).where(User.id.in_(ids))
        )
    ).all()
    out: dict[int, str] = {}
    for uid, display, username in rows:
        out[int(uid)] = (display or username or "").strip()
    return out


@router.get("", response_model=list[RoleRead])
async def list_roles(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[RoleRead]:
    """角色列表。

    **admin**：全部；**普通用户**：只返回自己加入的（决策 2026-11-23）。
    """
    stmt = select(Role).order_by(Role.id)
    if current_user.role != "admin":
        stmt = stmt.join(
            RoleMember, RoleMember.role_id == Role.id
        ).where(RoleMember.user_id == current_user.id)
    roles = list((await db.scalars(stmt)).all())
    counts = await _member_counts(db)
    names = await _owner_names(db, {r.created_by for r in roles if r.created_by})
    return [
        _to_read(r, counts.get(r.id, 0), names.get(r.created_by)) for r in roles
    ]


@router.post("", response_model=RoleRead, status_code=201)
async def create_role(
    payload: RoleCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> RoleRead:
    """新建角色组（admin）。"""
    name = payload.name.strip()
    if not name:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "角色名不能为空"
        )
    exists = await db.scalar(select(Role.id).where(Role.name == name))
    if exists is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"角色名已存在：{name}"
        )
    role = Role(
        name=name,
        description=payload.description.strip(),
        created_by=_.id,
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return _to_read(role, 0, None)


@router.patch("/{role_id}", response_model=RoleRead)
async def update_role(
    role_id: int,
    payload: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> RoleRead:
    """改名 / 改描述（admin）。"""
    role = await db.get(Role, role_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "角色不存在")
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "角色名不能为空"
            )
        dup = await db.scalar(
            select(Role.id).where(Role.name == name, Role.id != role_id)
        )
        if dup is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"角色名已存在：{name}"
            )
        role.name = name
    if payload.description is not None:
        role.description = payload.description.strip()
    await db.commit()
    await db.refresh(role)
    counts = await _member_counts(db)
    names = await _owner_names(db, {role.created_by} if role.created_by else set())
    return _to_read(role, counts.get(role.id, 0), names.get(role.created_by))


@router.delete("/{role_id}", status_code=204)
async def delete_role(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> None:
    """删角色（admin）。成员关系级联删除，共享关系随之消失。"""
    role = await db.get(Role, role_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "角色不存在")
    await db.delete(role)
    await db.commit()


@router.get("/{role_id}/members", response_model=list[RoleMemberRead])
async def list_members(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[RoleMemberRead]:
    """成员列表。

    普通用户只能看**自己加入的**角色的成员（否则等于泄露角色目录）。
    """
    role = await db.get(Role, role_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "角色不存在")
    if current_user.role != "admin":
        mine = await db.scalar(
            select(RoleMember.user_id).where(
                RoleMember.role_id == role_id,
                RoleMember.user_id == current_user.id,
            )
        )
        if mine is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "角色不存在")
    rows = (
        await db.execute(
            select(User.id, User.username, User.display_name, User.email)
            .join(RoleMember, RoleMember.user_id == User.id)
            .where(RoleMember.role_id == role_id)
            .order_by(User.username)
        )
    ).all()
    return [
        RoleMemberRead(
            user_id=int(uid),
            username=username or "",
            display_name=display_name or "",
            email=email or "",
        )
        for uid, username, display_name, email in rows
    ]


@router.post("/{role_id}/members", response_model=list[RoleMemberRead])
async def add_members(
    role_id: int,
    payload: RoleMembersAdd,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[RoleMemberRead]:
    """加成员（admin，可批量）。"""
    role = await db.get(Role, role_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "角色不存在")
    if not payload.user_ids:
        return await list_members(role_id, db, _)
    users = (
        await db.scalars(select(User).where(User.id.in_(payload.user_ids)))
    ).all()
    for u in users:
        exists = await db.scalar(
            select(RoleMember.user_id).where(
                RoleMember.role_id == role_id, RoleMember.user_id == u.id
            )
        )
        if exists is None:
            db.add(
                RoleMember(
                    role_id=role_id, user_id=u.id, added_by=_.id
                )
            )
    await db.commit()
    return await list_members(role_id, db, _)


@router.delete("/{role_id}/members/{user_id}", status_code=204)
async def remove_member(
    role_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> None:
    """移成员（admin）。"""
    row = await db.scalar(
        select(RoleMember).where(
            RoleMember.role_id == role_id, RoleMember.user_id == user_id
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "该成员不在此角色中")
    await db.delete(row)
    await db.commit()
