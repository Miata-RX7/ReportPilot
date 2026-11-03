"""清理超出上限的模板历史版本（每个模板只保留最近 10 个）。

背景（2026-11-03）：版本历史此前**没有任何上限**，ONLYOFFICE 每保存一次就落一个
新文件、记一个新版本。一份 3MB 的文档编辑十几次就是几十 MB，而绝大多数历史
版本根本不会再被打开。上线后由 `template_service.prune_old_versions` 自动裁剪，
本迁移负责把**存量**超额的部分一次性清掉。

裁剪规则（与运行时保持一致）：
- 每个模板按 version_no 倒序保留最近 10 个
- **当前版本不删**（word_templates.current_version_id）
- **被历史报告引用的版本不删**（reports.version_id）—— 删了老报告无法重渲染
  （reports.version_id 的外键没有 ondelete=CASCADE，直接删会触发 FK 约束错误，
  这里显式排除，双保险）

连带删除占位符绑定（template_bindings.version_id 外键已 CASCADE，这里显式删
更保险）与磁盘上的文档文件 —— 文件只在「裁剪后没有任何版本再引用它」时才删。

Revision ID: 0020
Revises: 0019
"""

import logging
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import bindparam

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

KEEP = 10


def upgrade() -> None:
    conn = op.get_bind()

    # 1) 找出每个模板超出 KEEP 个的最旧版本（排除受保护的两类）
    rows = conn.execute(
        sa.text(
            """
            SELECT v.id, v.version_no, v.file_path
            FROM template_versions v
            WHERE v.id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY template_id ORDER BY version_no DESC
                           ) AS rn
                    FROM template_versions
                ) ranked
                WHERE ranked.rn > :keep
            )
            AND v.id NOT IN (
                SELECT current_version_id FROM word_templates
                WHERE current_version_id IS NOT NULL
            )
            AND v.id NOT IN (
                SELECT version_id FROM reports WHERE version_id IS NOT NULL
            )
            """
        ),
        {"keep": KEEP},
    ).fetchall()

    if not rows:
        logger.info("没有超出上限的模板版本，无需清理")
        return

    ids = [r.id for r in rows]
    # 2) 裁剪后仍被引用的文件路径（这些文件不能删）
    keep_paths = {
        p
        for (p,) in conn.execute(
            sa.text(
                """
                SELECT DISTINCT file_path FROM template_versions
                WHERE id NOT IN :ids AND file_path IS NOT NULL
                """
            ),
            {"ids": tuple(ids)},
        ).fetchall()
    }
    # 模板自身的当前快照路径同样不能删
    for (p,) in conn.execute(
        sa.text(
            "SELECT file_path FROM word_templates WHERE file_path IS NOT NULL"
        )
    ).fetchall():
        keep_paths.add(p)

    # 3) 删绑定（外键已 CASCADE，显式删更保险）
    conn.execute(
        sa.text("DELETE FROM template_bindings WHERE version_id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        ),
        {"ids": tuple(ids)},
    )
    # 4) 删版本行
    conn.execute(
        sa.text("DELETE FROM template_versions WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        ),
        {"ids": tuple(ids)},
    )

    # 5) 删磁盘文件（best-effort：删不掉不该让迁移失败）
    removed_files = 0
    for _id, _no, file_path in rows:
        if not file_path or file_path in keep_paths:
            continue
        try:
            Path(file_path).unlink(missing_ok=True)
            removed_files += 1
        except OSError as exc:
            logger.warning("清理模板文件失败 path=%s err=%s", file_path, exc)

    logger.info(
        "模板版本裁剪完成：删除 %d 个版本、%d 个文件（每模板保留 %d 个）",
        len(ids),
        removed_files,
        KEEP,
    )


def downgrade() -> None:
    """无法回滚：被删除的历史版本与文件不可恢复。

    这里**故意不做任何事** —— 写一个假的「重建」比什么都不做更危险：
    它会让人以为回滚能恢复数据，实际恢复不了。
    """
    logger.info("版本裁剪不可回滚（删除的历史版本与文件无法恢复）")
