"""报告任务删除的级联清理（2026-10-22 用户需求）。

删除任务时要把**执行历史（Report 实例）及相关文件**一并清掉。
此前后端是硬约束「必须从未执行过、且无实例」，导致执行过的任务永远删不掉。

关注四件事：
1. 停用任务才允许删（停用 = 不再产生新实例；删除 = 连历史一起抹掉，两步分开）
2. 执行过的任务**现在也能删**（这是本次改动的核心）
3. Report 实例与子表（report_audits / push_records / ai_call_logs）都被清掉
4. 生成的报告 docx 文件从磁盘上删除；文件删不掉不影响删除本身
"""

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.models.ai_call import AiCallLog
from app.models.report import PushRecord, Report, ReportAudit
from app.models.task import ReportTask
from app.models.template import WordTemplate
from app.services import task_service
from app.services.task_service import TaskError


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _make_task(db, *, active: bool = False) -> ReportTask:
    tpl = WordTemplate(
        name="tpl",
        file_path="/tmp/tpl.docx",
        owner_id=1,
    )
    db.add(tpl)
    await db.flush()
    task = ReportTask(
        name="月报任务",
        template_id=tpl.id,
        cron_expr="0 8 1 * *",
        is_active=active,
        confirmer_email="ops@test.local",
        created_by=1,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def _make_report(db, task, *, file_path: str | None = None) -> Report:
    rep = Report(
        task_id=task.id,
        template_id=1,
        period="2026-09",
        status="done",
        facts_json=[],
        insights_json={},
        file_path=file_path,
    )
    db.add(rep)
    await db.flush()
    db.add(ReportAudit(report_id=rep.id, action="created", auditor_id=1))
    db.add(PushRecord(report_id=rep.id, attempt=1, action="push"))
    db.add(AiCallLog(report_id=rep.id, kind="generate", slot="summary"))
    await db.commit()
    await db.refresh(rep)
    return rep


class TestTaskDelete:
    async def test_active_task_cannot_be_deleted(self, db) -> None:
        """启用中的任务不允许删 —— 必须先停用。"""
        task = await _make_task(db, active=True)
        with pytest.raises(TaskError, match="停用"):
            await task_service.delete_task(db, task)

    async def test_executed_task_can_now_be_deleted(self, db) -> None:
        """执行过的任务现在也能删（这是本次改动的核心）。

        旧行为：last_run_at 非空 -> 抛「该任务已执行过，不能删除」。
        """
        task = await _make_task(db)
        task.last_run_at = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
        await db.commit()
        await _make_report(db, task)

        await task_service.delete_task(db, task)

        assert await db.get(ReportTask, task.id) is None

    async def test_deletes_report_instances_and_children(self, db) -> None:
        """实例行与子表（审计 / 推送记录 / AI 调用日志）都要清掉。"""
        task = await _make_task(db)
        rep = await _make_report(db, task)

        await task_service.delete_task(db, task)

        assert await db.get(Report, rep.id) is None
        assert (
            await db.scalar(
                select(func.count(ReportAudit.id)).where(ReportAudit.report_id == rep.id)
            )
            == 0
        )
        assert (
            await db.scalar(
                select(func.count(PushRecord.id)).where(PushRecord.report_id == rep.id)
            )
            == 0
        )
        assert (
            await db.scalar(
                select(func.count(AiCallLog.id)).where(AiCallLog.report_id == rep.id)
            )
            == 0
        )

    async def test_deletes_report_file_on_disk(self, db, tmp_path: Path) -> None:
        """生成的报告 docx 要从磁盘上删掉。"""
        task = await _make_task(db)
        f = tmp_path / "report_1_2026-09.docx"
        f.write_bytes(b"x")
        await _make_report(db, task, file_path=str(f))

        await task_service.delete_task(db, task)

        assert not f.exists(), "报告文件应被删除"

    async def test_missing_file_does_not_break_delete(self, db, tmp_path: Path) -> None:
        """文件已被人工删掉时，删除任务仍要成功。"""
        task = await _make_task(db)
        await _make_report(db, task, file_path=str(tmp_path / "nope.docx"))

        await task_service.delete_task(db, task)

        assert await db.get(ReportTask, task.id) is None

    async def test_task_without_reports_deletes_cleanly(self, db) -> None:
        """没有实例的任务也能删（老行为也支持，别回归）。"""
        task = await _make_task(db)
        await task_service.delete_task(db, task)
        assert await db.get(ReportTask, task.id) is None