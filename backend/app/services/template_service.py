"""模板权限（属主/授权）与版本管理。

权限模型：
- admin / 属主(owner) -> manage（编辑 + 绑定 + 授权 + 转移 + 删除）
- 被授权 edit      -> 编辑文档 + 配置绑定
- 被授权 view      -> 只读
"""

import logging

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.template import (
    TemplateBinding,
    TemplateGrant,
    TemplateVersion,
    WordTemplate,
)
from app.models.user import User

logger = logging.getLogger(__name__)

PERMISSION_ORDER = {"view": 1, "edit": 2, "manage": 3}


def has_permission(permission: str | None, required: str) -> bool:
    if permission is None:
        return False
    return PERMISSION_ORDER.get(permission, 0) >= PERMISSION_ORDER[required]


async def resolve_permission(
    db: AsyncSession, template: WordTemplate, user: User
) -> str | None:
    """返回当前用户对该模板的权限（manage/edit/view/None）。"""
    if user.role == "admin":
        return "manage"
    if template.owner_id is not None and template.owner_id == user.id:
        return "manage"
    grant = await db.scalar(
        select(TemplateGrant.permission).where(
            TemplateGrant.template_id == template.id,
            TemplateGrant.user_id == user.id,
        )
    )
    return grant


async def require_permission(
    db: AsyncSession, template: WordTemplate, user: User, required: str
) -> str:
    permission = await resolve_permission(db, template, user)
    if not has_permission(permission, required):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权访问该模板")
    return permission or "view"


async def list_accessible(
    db: AsyncSession, user: User, include_inactive: bool = False
) -> list[tuple[WordTemplate, str]]:
    """列出当前用户可见的模板及其权限（admin 见全部）。"""
    stmt = select(WordTemplate).order_by(WordTemplate.id)
    if not include_inactive:
        stmt = stmt.where(WordTemplate.is_active.is_(True))

    if user.role == "admin":
        templates = list((await db.scalars(stmt)).all())
        return [(t, "manage") for t in templates]

    grants = dict(
        (
            await db.execute(
                select(TemplateGrant.template_id, TemplateGrant.permission).where(
                    TemplateGrant.user_id == user.id
                )
            )
        ).all()
    )
    templates = list((await db.scalars(stmt)).all())
    result: list[tuple[WordTemplate, str]] = []
    for t in templates:
        if t.owner_id == user.id:
            result.append((t, "manage"))
        elif t.id in grants:
            result.append((t, grants[t.id]))
    return result


async def get_current_version(
    db: AsyncSession, template: WordTemplate
) -> TemplateVersion | None:
    if template.current_version_id is None:
        return None
    return await db.get(TemplateVersion, template.current_version_id)


async def list_versions(
    db: AsyncSession, template: WordTemplate
) -> list[TemplateVersion]:
    return list(
        (
            await db.scalars(
                select(TemplateVersion)
                .where(TemplateVersion.template_id == template.id)
                .order_by(TemplateVersion.version_no.desc())
            )
        ).all()
    )


async def get_version_or_404(
    db: AsyncSession, template: WordTemplate, version_id: int
) -> TemplateVersion:
    version = await db.get(TemplateVersion, version_id)
    if version is None or version.template_id != template.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模板版本不存在")
    return version


async def bindings_for_version(
    db: AsyncSession, version_id: int
) -> list[TemplateBinding]:
    return list(
        (
            await db.scalars(
                select(TemplateBinding).where(TemplateBinding.version_id == version_id)
            )
        ).all()
    )


async def count_bindings_by_version(
    db: AsyncSession,
) -> dict[int, int]:
    rows = await db.execute(
        select(TemplateBinding.version_id, func.count()).group_by(
            TemplateBinding.version_id
        )
    )
    return dict(rows.all())


async def create_version(
    db: AsyncSession,
    template: WordTemplate,
    *,
    file_path: str,
    placeholders: list[str],
    actor_id: int | None,
    source: str,
) -> tuple[TemplateVersion, list[str], list[str], int]:
    """新增版本并切换为当前版本；复制上一版本绑定并按占位符差异清理。

    返回 (新版本, removed, added, cleared_bindings)；不 commit。
    """
    prev_version_id = template.current_version_id
    prev_placeholders = list(template.placeholders_json or [])

    max_no = await db.scalar(
        select(func.max(TemplateVersion.version_no)).where(
            TemplateVersion.template_id == template.id
        )
    )
    version = TemplateVersion(
        template_id=template.id,
        version_no=(max_no or 0) + 1,
        file_path=file_path,
        placeholders_json=list(placeholders),
        created_by=actor_id,
        source=source,
    )
    db.add(version)
    await db.flush()

    new_set = set(placeholders)
    removed = [p for p in prev_placeholders if p not in new_set]
    added = [p for p in placeholders if p not in set(prev_placeholders)]

    cleared = 0
    if prev_version_id is not None:
        prev_bindings = await bindings_for_version(db, prev_version_id)
        for b in prev_bindings:
            if b.placeholder in new_set:
                db.add(
                    TemplateBinding(
                        version_id=version.id,
                        placeholder=b.placeholder,
                        binding_type=b.binding_type,
                        binding_ref_id=b.binding_ref_id,
                        binding_key=b.binding_key,
                        ai_config_json=b.ai_config_json,
                    )
                )
            else:
                cleared += 1

    template.current_version_id = version.id
    template.file_path = file_path
    template.placeholders_json = list(placeholders)
    await db.flush()
    logger.info(
        "模板新版本 template_id=%d version=%d source=%s removed=%d added=%d cleared=%d",
        template.id,
        version.version_no,
        source,
        len(removed),
        len(added),
        cleared,
    )
    return version, removed, added, cleared


async def replace_version_bindings(
    db: AsyncSession, version_id: int, bindings: list[TemplateBinding]
) -> int:
    """全量替换某版本的绑定，返回被清理的旧绑定数；不 commit。"""
    existing = await bindings_for_version(db, version_id)
    cleared = len(existing)
    if existing:
        await db.execute(
            delete(TemplateBinding).where(TemplateBinding.version_id == version_id)
        )
        await db.flush()
    for b in bindings:
        b.version_id = version_id
        db.add(b)
    await db.flush()
    return cleared


async def set_current_version(
    db: AsyncSession, template: WordTemplate, version: TemplateVersion
) -> None:
    """回滚：把指定历史版本设为当前版本（同步快照）。"""
    template.current_version_id = version.id
    template.file_path = version.file_path
    template.placeholders_json = list(version.placeholders_json or [])
    await db.flush()


async def granted_user_ids(db: AsyncSession, template_id: int) -> set[int]:
    rows = await db.scalars(
        select(TemplateGrant.user_id).where(TemplateGrant.template_id == template_id)
    )
    return set(rows.all())
