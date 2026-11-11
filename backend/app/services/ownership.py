"""指标 / 报告任务的归属与权限（2026-11-11）。

背景：指标与报告任务原先**完全不分用户** —— 所有人都能看到全部指标，
只有 admin 能改指标、但任何人都能改/删任务。现在统一为「每个人只看到自己创建的」。

规则：
- **admin 可见全部**；普通用户只看到自己的
- **公共资源**（owner_id IS NULL）所有登录用户可见只读
  （存量迁移后不会出现，留着作为将来「全局共享指标」的兜底）
- 写操作：owner 本人 或 admin
- **无权查看一律 404 而不是 403** —— 403 会泄露「这个 id 存在」

模板**不在此模块**：它本来就有 owner_id + template_grants 的三级权限
（view/edit/manage），逻辑不动，只在列表里补一个「创建人」字段。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


def is_admin(user: Any) -> bool:
    return getattr(user, "role", None) == "admin"


def metric_visible_to(metric: Any, user: Any) -> bool:
    """指标是否对 user 可见。"""
    if is_admin(user):
        return True
    owner_id = getattr(metric, "owner_id", None)
    if owner_id is None:
        return True  # 公共指标
    return owner_id == user.id


def metric_editable_by(metric: Any, user: Any) -> bool:
    """指标是否可被 user 修改/删除。"""
    if is_admin(user):
        return True
    owner_id = getattr(metric, "owner_id", None)
    return owner_id is not None and owner_id == user.id


def task_visible_to(task: Any, user: Any) -> bool:
    """报告任务是否对 user 可见。"""
    if is_admin(user):
        return True
    return getattr(task, "created_by", None) == user.id


def task_editable_by(task: Any, user: Any) -> bool:
    """报告任务是否可被 user 修改/删除/停用/触发。"""
    return task_visible_to(task, user)


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
