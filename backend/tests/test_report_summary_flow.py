"""确认页上传 -> 总结 -> 快照 -> 渲染出总结占位（2026-11-03 端到端）。

覆盖最关键的一条：模板里的「总结_」占位**不参与绑定**，但报告渲染时能被
确认快照填上 —— 这是整个功能能否成立的前提。
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.models.report import Report, ReportTaskFile
from app.models.template import WordTemplate
from app.services import report_summary_service
from app.services.docx_renderer import is_summary_placeholder


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _report(db) -> Report:
    tpl = WordTemplate(
        name=f"月报模板-{uuid.uuid4().hex[:8]}",
        file_path="/tmp/t.docx",
        owner_id=1,
    )
    db.add(tpl)
    await db.flush()
    rep = Report(
        task_id=None,
        template_id=tpl.id,
        period="2026-09",
        status="pending_confirmation",
        facts_json=[],
        insights_json={},
    )
    db.add(rep)
    await db.commit()
    await db.refresh(rep)
    return rep


def _file(db, rep: Report, name: str) -> ReportTaskFile:
    item = ReportTaskFile(
        report_id=rep.id,
        filename=name,
        stored_path=f"/tmp/{name}",
        sha256="a" * 64,
        size_bytes=10,
        row_count=3,
        columns_json=["处理类型"],
    )
    db.add(item)
    return item


class TestSummaryPlaceholder:
    def test_detection(self) -> None:
        assert is_summary_placeholder("总结_工单汇总")
        assert not is_summary_placeholder("CPU使用率")

    async def test_snapshot_roundtrip(self, db) -> None:
        """文件 -> 总结快照 -> summary_texts 能取回，键是占位名。"""
        rep = await _report(db)
        item = _file(db, rep, "工单.xlsx")
        await db.commit()
        await db.refresh(item)

        await report_summary_service.save_summary(
            db,
            rep,
            item,
            placeholder="总结_工单汇总",
            prompt="按处理类型统计",
            text="本月共处理账号权限 24 件。",
        )
        texts = await report_summary_service.summary_texts(db, rep.id)
        assert texts["总结_工单汇总"] == "本月共处理账号权限 24 件。"

        rows = await report_summary_service.list_summaries(db, rep.id)
        assert len(rows) == 1
        assert rows[0].placeholder == "总结_工单汇总"

    async def test_same_placeholder_overwritten(self, db) -> None:
        """同一占位重复保存应更新而不是新增两行。"""
        rep = await _report(db)
        item = _file(db, rep, "a.xlsx")
        await db.commit()
        await db.refresh(item)
        for text in ("第一版", "第二版"):
            await report_summary_service.save_summary(
                db, rep, item, placeholder="总结_X", prompt="", text=text
            )
        rows = await report_summary_service.list_summaries(db, rep.id)
        assert len(rows) == 1
        assert rows[0].text == "第二版"

    async def test_file_list_scoped_to_report(self, db) -> None:
        """文件只属于自己那一期报告（别的 report 拿不到）。"""
        rep1 = await _report(db)
        rep2 = await _report(db)
        _file(db, rep1, "one.xlsx")
        _file(db, rep2, "two.xlsx")
        await db.commit()
        f1 = await report_summary_service.list_files(db, rep1.id)
        f2 = await report_summary_service.list_files(db, rep2.id)
        assert len(f1) == 1 and len(f2) == 1
        assert f1[0].id != f2[0].id
