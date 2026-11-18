"""部分成功（NO_DATA 降级）的回归测试（2026-11-18）。

场景：模板里某个变量取不到数（数据源不可达 / 数据缺失）-> 不应让整份报告
失败，而应：
- 该占位在文档里被填成 NO_DATA
- 报告状态 = partial_success
- no_data_vars 记录清楚是哪个变量、哪个指标、什么原因
"""

from collections.abc import AsyncGenerator

import httpx
import pytest
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


class TestNoDataDegradation:
    async def test_compute_template_facts_ex_records_no_data(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """某个变量算不出来 -> 记进 no_data_vars，且**不抛错**。"""
        from app.models.metric import MetricDefinition
        from app.models.template import TemplateBinding, TemplateVersion, WordTemplate
        from app.services import preview_service
        from app.services.calc_engine import MetricComputeError
        from app.services.preview_service import compute_template_facts_ex

        client, session_factory = api
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
                placeholders_json=["ok_ph", "bad_ph"],
            )
            db.add(version)
            await db.flush()
            # ok_ph -> 指向一个真实存在的指标（data_source 为空 -> time 类型）
            time_metric = MetricDefinition(
                owner_id=1, name="上月月份", metric_type="time", is_active=True
            )
            db.add(time_metric)
            await db.flush()
            # bad_ph -> 指向一个 scalar 指标，但数据源不存在 -> 计算必失败
            bad_metric = MetricDefinition(
                owner_id=1,
                name="坏指标",
                metric_type="scalar",
                data_source_id=99999,  # 不存在的数据源
                query_config={"item_ids": ["1"], "host_ids": ["1"], "host_group_ids": ["1"]},
                is_active=True,
            )
            db.add(bad_metric)
            await db.flush()
            db.add(
                TemplateBinding(
                    version_id=version.id,
                    placeholder="ok_ph",
                    binding_type="metric",
                    binding_ref_id=time_metric.id,
                )
            )
            db.add(
                TemplateBinding(
                    version_id=version.id,
                    placeholder="bad_ph",
                    binding_type="metric",
                    binding_ref_id=bad_metric.id,
                )
            )
            await db.commit()

            # 让「坏指标」的取数抛 MetricComputeError（模拟数据缺失/无趋势数据）
            real_compute = preview_service.compute_metric
            real_loader = preview_service.load_adapter_and_name

            async def _boom(metric, period, adapter, source_name):
                if metric.name == "坏指标":
                    raise MetricComputeError("本期时间区间内无趋势数据")
                return await real_compute(metric, period, adapter, source_name)

            async def _fake_loader(db, ds_id):
                # 数据源 99999 并不存在 -> 换成假 loader，让取数走到 mock
                return None, "fake-ds"

            preview_service.compute_metric = _boom
            preview_service.load_adapter_and_name = _fake_loader
            try:
                # allow_no_data=True -> 不抛错，坏变量进 no_data_vars
                bindings, facts_by_ph, metrics_by_ph, no_data = (
                    await compute_template_facts_ex(
                        db, version, "2026-10", allow_no_data=True
                    )
                )
            finally:
                preview_service.compute_metric = real_compute
                preview_service.load_adapter_and_name = real_loader
            assert len(no_data) == 1, no_data
            assert no_data[0]["placeholder"] == "bad_ph"
            assert no_data[0]["metric_name"] == "坏指标"
            assert no_data[0]["reason"]
            assert "ok_ph" in facts_by_ph
            assert "bad_ph" not in facts_by_ph

    async def test_build_context_fills_no_data_placeholder(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """build_context 对无数据变量填 NO_DATA（而不是 KeyError）。"""
        from app.models.metric import MetricDefinition
        from app.models.template import TemplateBinding
        from app.services.preview_service import NO_DATA_PLACEHOLDER, build_context

        client, session_factory = api
        async with session_factory() as db:
            m = MetricDefinition(
                owner_id=1, name="坏指标", metric_type="scalar", data_source_id=1,
                query_config={}, is_active=True,
            )
            db.add(m)
            await db.flush()
            binding = TemplateBinding(
                version_id=1,
                placeholder="bad_ph",
                binding_type="metric",
                binding_ref_id=m.id,
            )
            no_data = [
                {"placeholder": "bad_ph", "metric_name": "坏指标", "reason": "无趋势数据"}
            ]
            ctx = build_context(
                [binding],
                {},  # 快照里没有 bad_ph（它被跳过了）
                {"bad_ph": m},
                {},
                no_data_vars=no_data,
            )
            assert ctx["bad_ph"] == NO_DATA_PLACEHOLDER == "NO_DATA"

    async def test_config_error_is_not_degraded(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession]]
    ) -> None:
        """**配置错误**（数据源不存在）不能降级成 NO_DATA —— 要让人去修配置。"""
        from app.models.metric import MetricDefinition
        from app.models.template import TemplateBinding, TemplateVersion, WordTemplate
        from app.services.preview_service import (
            PreviewError,
            compute_template_facts,
        )

        client, session_factory = api
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
                placeholders_json=["bad_ph"],
            )
            db.add(version)
            await db.flush()
            bad_metric = MetricDefinition(
                owner_id=1,
                name="坏指标",
                metric_type="scalar",
                data_source_id=99999,
                query_config={"item_ids": ["1"], "host_ids": ["1"], "host_group_ids": ["1"]},
                is_active=True,
            )
            db.add(bad_metric)
            await db.flush()
            db.add(
                TemplateBinding(
                    version_id=version.id,
                    placeholder="bad_ph",
                    binding_type="metric",
                    binding_ref_id=bad_metric.id,
                )
            )
            await db.commit()

            # 坏指标绑了不存在的数据源 99999 -> load_adapter 抛 HTTPException
            with pytest.raises(Exception):
                await compute_template_facts(db, version, "2026-10")
