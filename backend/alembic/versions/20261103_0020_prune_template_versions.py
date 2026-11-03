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

**实现注意**：全部用子查询 + 标量参数，**不用 IN (:ids) 元组**。
asyncpg 驱动不支持 SQLAlchemy 的 expanding=True 绑定（它用 $1 位置参数），
会报 ``syntax error at or near "$1"``（实测踩过）。

Revision ID: 0020
Revises: 0019
"""

import logging
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

KEEP = 10

# 排名子查询：按模板分组，version_no 倒序
_RANKED = """
SELECT id,
       ROW_NUMBER() OVER (
           PARTITION BY template_id ORDER BY version_no DESC
       ) AS rn
FROM template_versions
"""

# 受保护：当前版本 + 被报告引用的版本
_PROTECTED = """
id NOT IN (
    SELECT current_version_id FROM word_templates
    WHERE current_version_id IS NOT NULL
)
AND id NOT IN (
    SELECT version_id FROM reports WHERE version_id IS NOT NULL
)
"""

# 待删版本：每个模板超出 KEEP 个的最旧版本，排除受保护的
_DELETE_CANDIDATES = (
    "SELECT id, version_no, file_path, template_id FROM template_versions "
    "WHERE id IN (SELECT id FROM (" + _RANKED + ") ranked WHERE ranked.rn > :keep) "
    "AND " + _PROTECTED
)

# 真正删版本行的语句（上面那个是 SELECT 拿候选，这里才是 DELETE）
_DELETE_ROWS = (
    "DELETE FROM template_versions " 
    "WHERE id IN (SELECT id FROM (" + _RANKED + ") ranked WHERE ranked.rn > :keep) " 
    "AND " + _PROTECTED
)

# 裁剪后仍被引用的文件路径（这些文件不能删：共享路径 / 模板当前快照）
_REMAINING_PATHS = (
    "SELECT DISTINCT file_path FROM template_versions "
    "WHERE file_path IS NOT NULL "
    "AND id NOT IN (SELECT id FROM (" + _RANKED + ") ranked WHERE ranked.rn > :keep) "
    "AND " + _PROTECTED +
    " UNION SELECT file_path FROM word_templates WHERE file_path IS NOT NULL"
)


def upgrade() -> None:
    conn = op.get_bind()

    rows = conn.execute(sa.text(_DELETE_CANDIDATES), {"keep": KEEP}).fetchall()
    if not rows:
        logger.info("没有超出上限的模板版本，无需清理")
        return

    # 裁剪后仍被引用的路径：这些文件不能删
    keep_paths = {
        p for (p,) in conn.execute(sa.text(_REMAINING_PATHS), {"keep": KEEP}).fetchall()
    }

    # 删绑定（外键已 CASCADE，显式删更保险）
    conn.execute(
        sa.text(
            "DELETE FROM template_bindings WHERE version_id IN ("
            "SELECT id FROM (" + _RANKED + ") ranked WHERE ranked.rn > :keep) "
            "AND " + _PROTECTED.replace("id NOT IN", "version_id NOT IN")
        ),
        {"keep": KEEP},
    )
    # 删版本行
    conn.execute(sa.text(_DELETE_ROWS), {"keep": KEEP})

    # 删磁盘文件（best-effort：删不掉不该让迁移失败）
    removed_files = 0
    for _id, _no, file_path, _tid in rows:
        if not file_path or file_path in keep_paths:
            continue
        try:
            Path(file_path).unlink(missing_ok=True)
            removed_files += 1
        except OSError as exc:
            logger.warning("清理模板文件失败 path=%s err=%s", file_path, exc)

    logger.info(
        "模板版本裁剪完成：删除 %d 个版本、%d 个文件（每模板保留 %d 个）",
        len(rows),
        removed_files,
        KEEP,
    )


def downgrade() -> None:
    """无法回滚：被删除的历史版本与文件不可恢复。

    这里**故意不做任何事** —— 写一个假的「重建」比什么都不做更危险：
    它会让人以为回滚能恢复数据，实际恢复不了。
    """
    logger.info("版本裁剪不可回滚（删除的历史版本与文件无法恢复）")
