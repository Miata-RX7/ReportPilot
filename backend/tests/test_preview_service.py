"""preview_service 单元测试（SQLite 内存库 + FakeAdapter）。"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.api.deps as deps_module
from app.models import Base
from app.models.datasource import DataSource
from app.models.metric import MetricDefinition
from app.models.template import TemplateBinding, TemplateVersion, WordTemplate
from app.services.adapters.base import DataSourceAdapter
from app.services.crypto import encrypt
from app.services.preview_service import (
    AI_SUMMARY_PLACEHOLDER,
    PreviewError,
    format_fact_value,
    render_preview,
)
from tests.docx_factory import make_docx, read_docx_text

SEP = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())
PERIOD = "2026-09"


def trend_row(item_id: int, avg: float) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "clock": SEP + 3600,
        "num": 60,
        "value_min": avg,
        "value_avg": avg,
        "value_max": avg,
    }


class FakeAdapter(DataSourceAdapter):
    builds = 0

    def __init__(self, trend: list[dict[str, Any]]) -> None:
        super().__init__({}, None)
        FakeAdapter.builds += 1
        self._trend = trend

    async def test_connection(self) -> bool:
        return True

    async def list_host_groups(self) -> list[dict[str, Any]]:
        return [{"group_id": 4, "name": "Linux servers"}]

    async def list_hosts(self, group_ids: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "host_id": 1,
                "name": "web-01",
                "groups": [{"group_id": 4, "name": "Linux servers"}],
            }
        ]

    async def list_items(
        self, host_ids: list[str], value_types: list[int] | None = None
    ) -> list[dict[str, Any]]:
        return [
            {
                "item_id": 101,
                "name": "cpu",
                "key": "system.cpu.util",
                "host_id": 1,
                "host": "web-01",
                "value_type": 0,
                "units": "%",
            }
        ]

    async def fetch_latest_value(  # type: ignore[no-untyped-def]
        self, item_id: int, time_from: int | None = None, time_till: int | None = None
    ):
        return None


    async def get_item_meta(self, item_id: int):
        return None
    async def fetch_trend_data(
        self, item_ids: list[int], time_from: int, time_till: int
    ) -> list[dict[str, Any]]:
        return self._trend


@pytest.fixture
async def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path / "storage"))
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed(
    session: AsyncSession, tmp_path: Path, placeholders: list[str]
) -> tuple[WordTemplate, TemplateVersion, MetricDefinition]:
    ds = DataSource(
        name="生产 Zabbix",
        type="zabbix",
        config_json={"url": "http://z", "timeout": 5, "verify_ssl": False},
        secret_encrypted=encrypt("token"),
        secret_hint="****oken",
    )
    metric = MetricDefinition(
        name="cpu_usage",
        data_source_id=1,
        query_config={"host_group_ids": ["4"], "host_ids": ["1"], "item_ids": ["101"]},
        calc_formula={"agg": "avg", "compare": "none"},
        unit="%",
        period_type="monthly",
    )
    tpl_path = make_docx(
        tmp_path / "tpl.docx",
        paragraphs=[f"{{{{ {p} }}}}" for p in placeholders],
    )
    tpl = WordTemplate(name="月报模板")
    session.add_all([ds, metric, tpl])
    await session.flush()
    metric.data_source_id = ds.id
    version = TemplateVersion(
        template_id=tpl.id,
        version_no=1,
        file_path=str(tpl_path),
        placeholders_json=placeholders,
        source="upload",
    )
    session.add(version)
    await session.flush()
    tpl.current_version_id = version.id
    tpl.file_path = str(tpl_path)
    tpl.placeholders_json = placeholders
    await session.commit()
    return tpl, version, metric


def _patch_adapter(monkeypatch: pytest.MonkeyPatch, trend: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(
        deps_module, "build_adapter", lambda *a, **kw: FakeAdapter(trend)
    )


class TestFormatValue:
    def test_default_g(self) -> None:
        assert format_fact_value(210.0, None) == "210"
        assert format_fact_value(99.92, None) == "99.92"

    def test_fixed_precision(self) -> None:
        assert format_fact_value(210.0, 2) == "210.00"
        assert format_fact_value(99.9, 0) == "100"


class TestRenderPreview:
    async def test_unbound_variable_raises(
        self, session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, version, metric = await _seed(session, tmp_path, ["cpu", "summary"])
        session.add(
            TemplateBinding(
                version_id=version.id,
                placeholder="cpu",
                binding_type="metric",
                binding_ref_id=metric.id,
            )
        )
        await session.commit()
        _patch_adapter(monkeypatch, [trend_row(101, 30)])
        with pytest.raises(PreviewError, match="summary"):
            await render_preview(session, version, PERIOD)

    async def test_metric_failure_names_placeholder(
        self, session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, version, metric = await _seed(session, tmp_path, ["cpu"])
        session.add(
            TemplateBinding(
                version_id=version.id,
                placeholder="cpu",
                binding_type="metric",
                binding_ref_id=metric.id,
            )
        )
        await session.commit()
        _patch_adapter(monkeypatch, [])  # 空趋势 -> 计算失败
        with pytest.raises(PreviewError, match="cpu"):
            await render_preview(session, version, PERIOD)

    async def test_full_render_with_insight_placeholder(
        self, session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, version, metric = await _seed(session, tmp_path, ["cpu", "summary"])
        session.add_all(
            [
                TemplateBinding(
                    version_id=version.id,
                    placeholder="cpu",
                    binding_type="metric",
                    binding_ref_id=metric.id,
                ),
                TemplateBinding(
                    version_id=version.id,
                    placeholder="summary",
                    binding_type="insight",
                    binding_key="summary",
                ),
            ]
        )
        await session.commit()
        _patch_adapter(monkeypatch, [trend_row(101, 30)])
        out = await render_preview(session, version, PERIOD)
        text = read_docx_text(out)
        assert "30" in text
        assert AI_SUMMARY_PLACEHOLDER in text

    async def test_adapter_built_once_per_datasource(
        self, session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, version, metric = await _seed(session, tmp_path, ["m1", "m2"])
        metric2 = MetricDefinition(
            name="mem_usage",
            data_source_id=metric.data_source_id,
            query_config={"host_group_ids": ["4"], "host_ids": ["1"], "item_ids": ["101"]},
            calc_formula={"agg": "avg", "compare": "none"},
            unit="%",
            period_type="monthly",
        )
        session.add(metric2)
        await session.flush()
        session.add_all(
            [
                TemplateBinding(
                    version_id=version.id,
                    placeholder="m1",
                    binding_type="metric",
                    binding_ref_id=metric.id,
                ),
                TemplateBinding(
                    version_id=version.id,
                    placeholder="m2",
                    binding_type="metric",
                    binding_ref_id=metric2.id,
                ),
            ]
        )
        await session.commit()
        FakeAdapter.builds = 0
        _patch_adapter(monkeypatch, [trend_row(101, 30)])
        await render_preview(session, version, PERIOD)
        assert FakeAdapter.builds == 1  # 同数据源只构建一次 adapter

    async def test_display_precision_applied(
        self, session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, version, metric = await _seed(session, tmp_path, ["cpu"])
        metric.display_precision = 2
        session.add(
            TemplateBinding(
                version_id=version.id,
                placeholder="cpu",
                binding_type="metric",
                binding_ref_id=metric.id,
            )
        )
        await session.commit()
        _patch_adapter(monkeypatch, [trend_row(101, 30)])
        out = await render_preview(session, version, PERIOD)
        assert "30.00" in read_docx_text(out)

    async def test_unit_scale_outputs_converted_value_with_unit(
        self, session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """开启换算：占位符直接出「262.47 Kbps」（值 + 换算后单位）。"""
        _, version, metric = await _seed(session, tmp_path, ["cpu"])
        metric.unit = "bps"
        metric.unit_config = {"mode": "auto", "base": 1000, "suffix_unit": "bps"}
        session.add(
            TemplateBinding(
                version_id=version.id,
                placeholder="cpu",
                binding_type="metric",
                binding_ref_id=metric.id,
            )
        )
        await session.commit()
        _patch_adapter(monkeypatch, [trend_row(101, 262468.9041666667)])
        out = await render_preview(session, version, PERIOD)
        text = read_docx_text(out)
        assert "262.47 Kbps" in text  # 换算后默认 2 位小数
        assert "262468" not in text  # 原始长串不再出现

    async def test_no_unit_scale_keeps_raw_value_and_unit(
        self, session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未配置换算：行为与升级前一致（原值 + 原单位），不出现 Kbps。"""
        _, version, metric = await _seed(session, tmp_path, ["cpu"])
        metric.unit = "bps"
        session.add(
            TemplateBinding(
                version_id=version.id,
                placeholder="cpu",
                binding_type="metric",
                binding_ref_id=metric.id,
            )
        )
        await session.commit()
        _patch_adapter(monkeypatch, [trend_row(101, 262468.9041666667)])
        out = await render_preview(session, version, PERIOD)
        text = read_docx_text(out)
        assert "262469 bps" in text  # %g 默认 6 位有效数字
        assert "Kbps" not in text

    async def test_tight_unit_joined_without_space(
        self, session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """符号类单位（%%）紧贴数值，不产生 "30 %" 这种难看的间距。"""
        _, version, metric = await _seed(session, tmp_path, ["cpu"])
        session.add(
            TemplateBinding(
                version_id=version.id,
                placeholder="cpu",
                binding_type="metric",
                binding_ref_id=metric.id,
            )
        )
        await session.commit()
        _patch_adapter(monkeypatch, [trend_row(101, 30)])
        out = await render_preview(session, version, PERIOD)
        assert "30%" in read_docx_text(out)
