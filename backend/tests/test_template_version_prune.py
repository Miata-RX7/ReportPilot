"""模板版本裁剪（只保留最近 10 个版本）。

关注四件事：
1. 超过上限时，最旧的被删
2. **当前版本不删**（删了模板就没法用了）
3. **被历史报告引用的版本不删**（删了老报告无法重渲染）
4. 连带清理：占位符绑定、磁盘文件；共享路径的文件不能删
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.models.report import Report
from app.models.template import TemplateBinding, TemplateVersion, WordTemplate
from app.services import template_service
from app.services.template_service import MAX_VERSIONS_PER_TEMPLATE, prune_old_versions


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _template(db, name: str | None = None) -> WordTemplate:
    tpl = WordTemplate(
        name=name or f"tpl-{uuid.uuid4().hex[:8]}",
        file_path="/tmp/current.docx",
        owner_id=1,
    )
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return tpl


async def _add_versions(db, tpl: WordTemplate, n: int, *, dir_path=None):
    """造 n 个版本（version_no 从 1 递增），返回列表。"""
    import pathlib

    base = pathlib.Path(dir_path) if dir_path else pathlib.Path("/tmp/rp-test")
    base.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(1, n + 1):
        f = base / f"v{i}.docx"
        f.write_bytes(b"x")
        v = TemplateVersion(
            template_id=tpl.id,
            version_no=i,
            file_path=str(f),
            placeholders_json=[],
            source="upload",
        )
        db.add(v)
        await db.flush()
        out.append(v)
    await db.commit()
    return out


class TestPruneOldVersions:
    async def test_constant_is_ten(self) -> None:
        assert MAX_VERSIONS_PER_TEMPLATE == 10

    async def test_keeps_only_limit(self, db) -> None:
        """15 个版本 -> 留下 10 个，删掉最旧的 5 个。"""
        tpl = await _template(db)
        await _add_versions(db, tpl, 15)

        removed = await prune_old_versions(db, tpl)
        await db.commit()

        assert removed == [1, 2, 3, 4, 5]
        count = await db.scalar(
            select(func.count(TemplateVersion.id)).where(
                TemplateVersion.template_id == tpl.id
            )
        )
        assert count == 10

    async def test_keeps_newest_ones(self, db) -> None:
        """留下的是最新的 10 个，不是最旧的 10 个。"""
        tpl = await _template(db)
        versions = await _add_versions(db, tpl, 15)

        await prune_old_versions(db, tpl)
        await db.commit()

        left = set(
            await db.scalars(
                select(TemplateVersion.version_no).where(
                    TemplateVersion.template_id == tpl.id
                )
            )
        )
        assert left == set(range(6, 16))

    async def test_current_version_never_removed(self, db) -> None:
        """当前版本即使是最旧的也不能删。"""
        tpl = await _template(db)
        versions = await _add_versions(db, tpl, 15)
        tpl.current_version_id = versions[0].id  # 故意把当前版本设成最旧的
        await db.commit()

        await prune_old_versions(db, tpl)
        await db.commit()

        assert await db.get(TemplateVersion, versions[0].id) is not None

    async def test_version_referenced_by_report_kept(self, db) -> None:
        """被历史报告引用的版本不能删 —— 否则老报告无法重渲染。"""
        tpl = await _template(db)
        versions = await _add_versions(db, tpl, 15)
        rep = Report(
            task_id=None,
            template_id=tpl.id,
            version_id=versions[1].id,
            period="2026-09",
            status="done",
            facts_json=[],
            insights_json={},
        )
        db.add(rep)
        await db.commit()

        await prune_old_versions(db, tpl)
        await db.commit()

        assert await db.get(TemplateVersion, versions[1].id) is not None

    async def test_bindings_removed_with_version(self, db) -> None:
        """版本被删时，它的占位符绑定也要清掉。"""
        tpl = await _template(db)
        versions = await _add_versions(db, tpl, 15)
        b = TemplateBinding(
            version_id=versions[0].id,
            placeholder="CPU",
            binding_type="metric",
            binding_ref_id=1,
        )
        db.add(b)
        await db.commit()

        await prune_old_versions(db, tpl)
        await db.commit()

        left = await db.scalar(
            select(func.count(TemplateBinding.id)).where(
                TemplateBinding.version_id == versions[0].id
            )
        )
        assert left == 0

    async def test_file_deleted_when_not_shared(self, db, tmp_path) -> None:
        """版本被删，磁盘文件也随之删除。"""
        tpl = await _template(db)
        await _add_versions(db, tpl, 15, dir_path=tmp_path)
        victim = tmp_path / "v1.docx"
        assert victim.exists()

        await prune_old_versions(db, tpl)
        await db.commit()

        assert not victim.exists()

    async def test_shared_file_kept(self, db, tmp_path) -> None:
        """两个版本指向同一个文件时，不能删文件（会弄坏另一个版本）。"""
        tpl = await _template(db)
        versions = await _add_versions(db, tpl, 15, dir_path=tmp_path)
        shared = tmp_path / "v1.docx"
        # 让一个**存活**的版本（version_no=12，在保留窗口内）也指向它
        versions[11].file_path = str(shared)
        await db.commit()

        await prune_old_versions(db, tpl)
        await db.commit()

        assert shared.exists(), "共享路径的文件不应被删"

    async def test_noop_when_under_limit(self, db) -> None:
        """不满上限时什么都不做。"""
        tpl = await _template(db)
        await _add_versions(db, tpl, 3)

        removed = await prune_old_versions(db, tpl)
        await db.commit()

        assert removed == []
        count = await db.scalar(
            select(func.count(TemplateVersion.id)).where(
                TemplateVersion.template_id == tpl.id
            )
        )
        assert count == 3
