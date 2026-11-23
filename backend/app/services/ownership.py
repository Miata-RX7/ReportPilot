"""指标 / 报告任务的归属与权限（2026-11-11）。

背景：指标与报告任务原先**完全不分用户** —— 所有人都能看到全部指标，
只有 admin 能改指标、但任何人都能改/删任务。现在统一为「每个人只看到自己创建的」。

规则：
- **admin 可见全部**；普通用户看到自己的 + **同角色组成员的**（只读）
- **不再有「公共资源」**（owner_id IS NULL）：2026-11-23 起 owner_id 为
  NOT NULL，每个资源**必有属主**（存量无主指标已确认 0 条，迁移 0030 加约束）
- 写操作：owner 本人 或 admin（**同角色成员只有只读**）
- **无权查看一律 404 而不是 403** —— 403 会泄露「这个 id 存在」

「角色组」（共享圈）机制（2026-11-23）：
- 角色只圈**人**，不圈资源；资源仍归属个人
- 同组成员互相可见对方的指标/任务（只读）
- 共享范围以「属主 id 集合」的形式**挂在 user 对象上**
  （``user._share_scope``），由 :func:`attach_share_scope` 计算 —— 因为本模块
  的判定函数是**同步无 db** 的，这样不必给每个函数都加 db 参数

模板**不在此模块**：它本来就有 owner_id + template_grants 的三级权限
（view/edit/manage），逻辑不动，只在列表里补一个「创建人」字段。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


def is_admin(user: Any) -> bool:
    return getattr(user, "role", None) == "admin"


# --------------------------------------------------------------------------
# 角色组（共享圈）—— 共享范围
# --------------------------------------------------------------------------

#: 挂在 user 上的共享范围属性名：属主 id 集合（含用户自己）
SHARE_SCOPE_ATTR = "_share_scope"


def share_scope(user: Any) -> frozenset[int] | None:
    """当前用户的共享范围（可见的属主 id 集合）。

    ``None`` = **未计算**（保守：只看自己的，不会误放权）。
    由 :func:`attach_share_scope` 在请求入口挂载。
    """
    return getattr(user, SHARE_SCOPE_ATTR, None)


def in_share_scope(user: Any, owner_id: int | None) -> bool:
    """owner_id 是否落在用户的共享范围内（同角色组成员的资源）。"""
    if owner_id is None:
        return False
    scope = share_scope(user)
    return scope is not None and owner_id in scope


async def attach_share_scope(db: Any, user: Any) -> frozenset[int]:
    """计算并挂载用户的共享范围（属主 id 集合）。

    - admin 不需要（可见全部）-> 返回空集合
    - 普通用户 = 自己 + **所有同角色组的成员**
    - 一人可属多个角色 -> 并集

    一次查询取回「我加入的角色 -> 全部成员」，避免逐个资源查 EXISTS。
    """
    if is_admin(user):
        setattr(user, SHARE_SCOPE_ATTR, frozenset())
        return frozenset()

    from sqlalchemy import select

    from app.models.role import RoleMember

    my_roles = (
        await db.scalars(
            select(RoleMember.role_id).where(RoleMember.user_id == user.id)
        )
    ).all()
    ids: set[int] = {user.id}
    if my_roles:
        peers = (
            await db.scalars(
                select(RoleMember.user_id).where(
                    RoleMember.role_id.in_(list(my_roles))
                )
            )
        ).all()
        ids.update(int(u) for u in peers)
    scope = frozenset(ids)
    setattr(user, SHARE_SCOPE_ATTR, scope)
    return scope


def metric_visible_to(metric: Any, user: Any) -> bool:
    """指标是否对 user 可见（自己的 + 同角色组成员的，只读）。"""
    if is_admin(user):
        return True
    owner_id = getattr(metric, "owner_id", None)
    if owner_id == user.id:
        return True
    return in_share_scope(user, owner_id)


def metric_editable_by(metric: Any, user: Any) -> bool:
    """指标是否可被 user 修改/删除（owner 本人或 admin；同角色成员只读）。"""
    if is_admin(user):
        return True
    owner_id = getattr(metric, "owner_id", None)
    return owner_id is not None and owner_id == user.id


def task_visible_to(task: Any, user: Any) -> bool:
    """报告任务是否对 user 可见（自己的 + 同角色组成员的，只读）。"""
    if is_admin(user):
        return True
    created_by = getattr(task, "created_by", None)
    if created_by == user.id:
        return True
    return in_share_scope(user, created_by)


def task_editable_by(task: Any, user: Any) -> bool:
    """报告任务是否可被 user 修改/删除/停用/触发。

    **可见 ≠ 可编辑**（2026-11-23）：原先是 ``task_visible_to`` 直接返回，
    引入「同角色只读共享」后必须拆开 —— 否则共享出去的任务会被同角色成员改掉。
    """
    if is_admin(user):
        return True
    return getattr(task, "created_by", None) == user.id


def ensure_metric_visible(metric: Any, user: Any) -> None:
    if not metric_visible_to(metric, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "指标不存在")


def ensure_metric_editable(metric: Any, user: Any) -> None:
    if not metric_editable_by(metric, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权修改他人的指标")


def ensure_task_visible(task: Any, user: Any) -> None:
    if not task_visible_to(task, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")


def ensure_task_editable(task: Any, user: Any) -> None:
    if not task_editable_by(task, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权操作他人的任务")


def owner_names_for_admin(
    owner_ids: set[int], names: dict[int, str], user: Any
) -> dict[int, str]:
    """只有 admin 能拿到「创建人」名字映射；普通用户拿到空 dict。

    规则是「只有超级用户能看到创建人」——所以在**后端**就掐掉，
    不依赖前端隐藏（前端隐藏只是显示层，API 仍会泄露）。
    """
    if not is_admin(user):
        return {}
    return {oid: names[oid] for oid in owner_ids if oid in names}
