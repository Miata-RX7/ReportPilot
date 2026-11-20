"""效率价值计算与接口的回归测试（2026-11-20）。"""

from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.deps import get_current_user
from app.database import get_db
from app.main import app
from app.models import Base
from app.models.metric import MetricDefinition
from app.models.report import Report, ReportSummary
from app.models.task import ReportTask
from app.models.template import TemplateBinding, TemplateVersion, WordTemplate
from app.models.user import User


@pytest_asyncio.fixture
async def api() -> AsyncGenerator[tuple[AsyncClient, async_sessionmaker[AsyncSession]], None]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    user = User(
        id=1,
        username="tester",
        email="tester@test.local",
        display_name="Tester",
        source="local",
        role="admin",
        is_active=True,
    )
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, session_factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed(
    session_factory,
    *,
    scalar: int = 0,
    curve: int = 0,
    insight: int = 0,
    runs: list[str] = (),
    excel_summary: bool = False,
) -> int:
    """造一个任务 + 模板 + 绑定 + 若干报告实例，返回 task_id。"""
    async with session_factory() as db:
        tpl = WordTemplate(
            name="T", owner_id=1, file_path="/tmp/t.docx", is_active=True
        )
        db.add(tpl)
        await db.flush()
        version = TemplateVersion(
            template_id=tpl.id,
            version_no=1,
            file_path="/tmp/t-v1.docx",
            placeholders_json=["a"],
        )
        db.add(version)
        await db.flush()
        tpl.current_version_id = version.id

        for i in range(scalar):
            m = MetricDefinition(
                owner_id=1, name=f"s{i}", metric_type="scalar", is_active=True
            )
            db.add(m)
            await db.flush()
            db.add(
                TemplateBinding(
                    version_id=version.id,
                    placeholder=f"s{i}",
                    binding_type="metric",
                    binding_ref_id=m.id,
                )
            )
        for i in range(curve):
            m = MetricDefinition(
                owner_id=1, name=f"c{i}", metric_type="curve", is_active=True
            )
            db.add(m)
            await db.flush()
            db.add(
                TemplateBinding(
                    version_id=version.id,
                    placeholder=f"c{i}",
                    binding_type="metric",
                    binding_ref_id=m.id,
                )
            )
        for i in range(insight):
            db.add(
                TemplateBinding(
                    version_id=version.id,
                    placeholder=f"sum_{i}",
                    binding_type="insight",
                    binding_key=f"sum_{i}",
                )
            )

        task = ReportTask(
            name="T1",
            template_id=tpl.id,
            cron_expr="0 9 1 * *",
            period_granularity="month",
            period_offset=0,
            confirmer_email="a@b.c",
            recipient_emails=["r@b.c"],
            created_by=1,
            is_active=True,
        )
        db.add(task)
        await db.flush()
        task_id = task.id

        first_report = None
        for st in runs:
            rep = Report(
                task_id=task_id,
                template_id=tpl.id,
                version_id=version.id,
                source="manual",
                period="2026-10",
                status=st,
                recipient_emails=["r@b.c"],
            )
            db.add(rep)
            await db.flush()
            if first_report is None:
                first_report = rep.id

        if excel_summary and first_report is not None:
            from app.models.report import ReportTaskFile

            f = ReportTaskFile(
                report_id=first_report,
                filename="工作内容.xlsx",
                stored_path="/tmp/x.xlsx",
                size_bytes=10,
                sha256="0" * 64,
            )
            db.add(f)
            await db.flush()
            db.add(
                ReportSummary(
                    report_id=first_report,
                    file_id=f.id,
                    placeholder="sum_0",
                    prompt="p",
                    text="t",
                )
            )
        await db.commit()
        return task_id


class TestEfficiency:
    async def test_counts_and_manual_baseline(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """构成统计 + 人工基线公式。"""
        client, sf = api
        await _seed(sf, scalar=3, curve=2, insight=1, runs=["pushed"])
        r = await client.get("/api/efficiency/value")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["summary"]["task_count"] == 1
        t = d["tasks"][0]
        assert t["scalar_count"] == 3
        assert t["curve_count"] == 2
        assert t["insight_count"] == 1
        # 人工每次 = 3×1 + 2×2 + 1×5 = 12
        assert t["manual_per_run"] == 12
        # 平台一次性 = 3×2 + 2×4 + 1×2 = 16
        assert t["platform_setup"] == 16
        assert t["platform_per_run"] == 0
        assert t["run_count"] == 1
        # 累计 = 1×12 − 16 − 0 = −4（还没回本）
        assert t["total_saved"] == -4

    async def test_only_success_status_counts(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """执行次数只算成功终态。"""
        client, sf = api
        await _seed(
            sf,
            scalar=1,
            runs=["pushed", "confirmed_no_push", "failed", "pending_confirmation"],
        )
        r = await client.get("/api/efficiency/value")
        t = r.json()["tasks"][0]
        assert t["run_count"] == 2, t

    async def test_excel_summary_adds_confirm_phase(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """上传过 Excel 并生成摘要 -> 确认阶段计入（人工 +15、平台 +3）。"""
        client, sf = api
        await _seed(sf, scalar=1, runs=["pushed"], excel_summary=True)
        r = await client.get("/api/efficiency/value")
        t = r.json()["tasks"][0]
        assert t["has_excel_summary"] is True
        # 人工每次 = 1×1 + 15 = 16
        assert t["manual_per_run"] == 16
        # 平台每次 = 3
        assert t["platform_per_run"] == 3

    async def test_breakeven_and_saved(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """盈亏平衡与累计节省。"""
        client, sf = api
        await _seed(sf, scalar=10, runs=["pushed"] * 5)
        r = await client.get("/api/efficiency/value")
        t = r.json()["tasks"][0]
        assert t["manual_per_run"] == 10
        assert t["platform_setup"] == 20
        assert t["saved_per_run"] == 10
        # 回本 = ceil(20/10) = 2 次
        assert t["breakeven_runs"] == 2
        # 累计 = 5×10 − 20 = 30
        assert t["total_saved"] == 30
        assert r.json()["summary"]["total_saved_minutes"] == 30

    async def test_mine_is_scoped_to_current_user(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """mine 只含当前用户名下的任务。"""
        client, sf = api
        await _seed(sf, scalar=1, runs=["pushed"])
        r = await client.get("/api/efficiency/value")
        d = r.json()
        assert len(d["tasks"]) == 1
        assert len(d["mine"]) == 1
        assert d["mine"][0]["is_mine"] is True
        assert d["mine_summary"]["task_count"] == 1
