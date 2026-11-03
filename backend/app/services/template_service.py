"""模板权限（属主/授权）与版本管理。

权限模型：
- admin / 属主(owner) -> manage（编辑 + 绑定 + 授权 + 转移 + 删除）
- 被授权 edit      -> 编辑文档 + 配置绑定
- 被授权 view      -> 只读
"""

import logging

from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.docx_renderer import SUMMARY_PLACEHOLDER_PREFIX
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
    # 总结类占位（summary_ 前缀）不参与绑定，不计入绑定数：
    # 否则「绑定进度」的分母已经排除了它，分子还是它 -> 总是不能用。
    rows = await db.execute(
        select(TemplateBinding.version_id, func.count())
        .where(
            ~TemplateBinding.placeholder.startswith(SUMMARY_PLACEHOLDER_PREFIX)
        )
        .group_by(TemplateBinding.version_id)
    )
    return dict(rows.all())


#: 每个模板保留的历史版本数上限（超出后自动裁剪最旧的）
MAX_VERSIONS_PER_TEMPLATE = 10


async def prune_old_versions(
    db: AsyncSession,
    template: WordTemplate,
    *,
    keep: int = MAX_VERSIONS_PER_TEMPLATE,
) -> list[int]:
    """裁剪超出上限的旧版本，返回被删除的版本号（从旧到新）。

    规则：从新到旧数满 ``keep`` 个就停，其余进入候选。候选里再排除两类：

    - **当前版本**（``template.current_version_id``）—— 删了模板就没法用了
    - **被历史报告引用的版本**（``reports.version_id``）—— 删了那些老报告就无法
      重渲染、无法回溯当时用的到底是哪一版模板

    第 2 条还有数据库层兜底：``reports.version_id`` 的外键**没有** ondelete=CASCADE，
    直接删会触发 FK 约束错误。显式排除 + 依赖该约束，双保险。

    连带清理：该版本的占位符绑定、磁盘上的文档文件。**文件只在裁剪后没有任何
    版本再引用它时才删** —— 共享路径时删了会连带弄坏另一个版本。
    """
    from app.models.report import Report

    rows = list(
        (
            await db.scalars(
                select(TemplateVersion)
                .where(TemplateVersion.template_id == template.id)
                .order_by(TemplateVersion.version_no.desc())
            )
        ).all()
    )
    if len(rows) <= keep:
        return []

    candidates = rows[keep:]
    protected: set[int] = set()
    if template.current_version_id:
        protected.add(int(template.current_version_id))
    referenced = await db.scalars(
        select(Report.version_id).where(
            Report.version_id.in_([v.id for v in candidates])
        )
    )
    protected.update(int(r) for r in referenced if r is not None)

    to_delete = [v for v in candidates if v.id not in protected]

    # **先算一次**裁剪后仍被引用的文件路径集合。
    # 不能边删边查：若 A、B 两个版本指向同一文件且都在待删列表里，先删 A 后
    # 再查 B 就看不到 A 了，会把共享文件误删（实测踩过）。
    remaining_paths = {
        p
        for (p,) in await db.execute(
            select(TemplateVersion.file_path).where(
                TemplateVersion.template_id == template.id,
                TemplateVersion.id.notin_([v.id for v in to_delete]),
            )
        )
        if p
    }
    if template.file_path:
        remaining_paths.add(template.file_path)

    removed: list[int] = []
    for v in to_delete:
        file_path = v.file_path
        await db.execute(
            delete(TemplateBinding).where(TemplateBinding.version_id == v.id)
        )
        await db.delete(v)
        removed.append(v.version_no)
        if file_path and file_path not in remaining_paths:
            try:
                Path(file_path).unlink(missing_ok=True)
            except OSError as exc:
                # 文件删不掉不该让版本裁剪失败 —— 库里已删干净，残留一个没人引用
                # 的文件比整个操作回滚好
                logger.warning(
                    "版本裁剪后清理模板文件失败 path=%s err=%s", file_path, exc
                )

    await db.flush()
    if removed:
        # 升序返回（从旧到新），日志更好读
        removed.sort()
        logger.info(
            "模板版本裁剪 template_id=%d 保留 %d 个，删除版本号 %s",
            template.id,
            keep,
            removed,
        )
    return removed

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
    # 版本上限：只保留最近 MAX_VERSIONS_PER_TEMPLATE 个历史版本。
    # 放在 create_version 里而不是调用方，保证「谁建版本谁触发裁剪」，不会漏。
    await prune_old_versions(db, template, keep=MAX_VERSIONS_PER_TEMPLATE)

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
