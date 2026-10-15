"""报告列表 limit 参数（执行记录只取最近 N 条）。"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_current_user
from app.database import get_db
from app.main import app
from app.models.base import Base
from app.models.report import Report
from app.models.task import ReportTask
from app.models.template import WordTemplate
from app.models.user import User


@pytest_asyncio.fixture
async def api(fake_user: User):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: fake_user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed(factory, count: int) -> tuple[int, list[int]]:
    """建模板+任务，插入 count 个报告（用测试内存库，不碰真实 DB）。"""
    async with factory() as s:
        tpl = WordTemplate(
            name="月报模板", owner_id=1, placeholders_json=[], current_version_id=None
        )
        s.add(tpl)
        await s.flush()
        task = ReportTask(
            name="任务A",
            template_id=tpl.id,
            cron_expr="0 9 1 * *",
            confirmer_email="c@d.com",
            recipient_emails=["a@b.com"],
            is_active=True,
            created_by=1,
        )
        s.add(task)
        await s.flush()
        ids: list[int] = []
        for i in range(count):
            r = Report(
                task_id=task.id,
                template_id=tpl.id,
                source="cron",
                period=f"2026-{i + 1:02d}",
                status="pushed",
                recipient_emails=["a@b.com"],
                confirmer_email="c@d.com",
                facts_json=[],
                insights_json={},
            )
            s.add(r)
            await s.flush()
            ids.append(r.id)
        await s.commit()
        return task.id, ids


@pytest.mark.asyncio
async def test_limit_returns_latest_n(api) -> None:
    client, factory = api
    task_id, ids = await _seed(factory, 15)
    r = await client.get("/api/reports", params={"limit": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 10, f"expected 10, got {len(body)}"
    # 按 id 倒序取最新 10 条
    assert [x["id"] for x in body] == sorted(ids, reverse=True)[:10]
    assert all(x["task_id"] == task_id for x in body)


@pytest.mark.asyncio
async def test_limit_with_task_filter(api) -> None:
    client, factory = api
    task_id, _ = await _seed(factory, 12)
    r = await client.get("/api/reports", params={"task_id": task_id, "limit": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 10
    assert all(x["task_id"] == task_id for x in body)


@pytest.mark.asyncio
async def test_no_limit_returns_all(api) -> None:
    client, factory = api
    await _seed(factory, 3)
    r = await client.get("/api/reports")
    assert r.status_code == 200
    assert len(r.json()) == 3


@pytest.mark.asyncio
async def test_limit_out_of_range_rejected(api) -> None:
    client, _ = api
    assert (await client.get("/api/reports", params={"limit": 0})).status_code == 422
    assert (await client.get("/api/reports", params={"limit": 500})).status_code == 422