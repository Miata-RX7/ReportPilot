"""时间指标类型（metric_type=time）测试。

与前端 src/utils/time.spec.ts 同批用例，锁定两侧语义一致。
"""

from datetime import datetime

import pytest
import pytest_asyncio
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base

from app.database import AsyncSessionLocal
from app.models.metric import MetricDefinition
from app.schemas.metric import MetricCreate
from app.services import calc_engine
from app.services.calc_engine import (
    MetricComputeError,
    compute_metric,
    format_time_modules,
    quick_target_time,
    resolve_time_target,
    shift_time,
)
from app.schemas.metric import TimeConfig


def make_metric(
    modules: list[str] | None = None,
    *,
    zero_pad: bool = True,
    offset_value: int = 0,
    offset_unit: str = "day",
    metric_id: int = 9001,
) -> MetricDefinition:
    return MetricDefinition(
        id=metric_id,
        name="now",
        data_source_id=None,
        unit="",
        display_precision=None,
        metric_type="time",
        query_config={},
        type_config={
            "time_config": {
                "format_modules": modules if modules is not None else ["年", "-", "月"],
                "zero_pad": zero_pad,
                "offset_value": offset_value,
                "offset_unit": offset_unit,
            }
        },
        is_active=True,
    )


# --------------------------------------------------------------------------
# 格式化
# --------------------------------------------------------------------------
class TestFormatTimeModules:
    def test_user_example(self) -> None:
        """用户示例：2026-10 减 1 月 -> 2026-09。"""
        target = shift_time(datetime(2026, 10, 8, 14, 30), -1, "month")
        assert format_time_modules(["年", "-", "月"], target) == "2026-09"

    def test_zero_pad(self) -> None:
        d = datetime(2026, 1, 5, 9, 8, 7)
        assert format_time_modules(["年", "-", "月", "-", "日"], d) == "2026-01-05"
        assert format_time_modules(["时", ":", "分", ":", "秒"], d) == "09:08:07"

    def test_no_zero_pad(self) -> None:
        d = datetime(2026, 1, 5, 9, 8, 7)
        assert format_time_modules(["年", "-", "月", "-", "日"], d, False) == "2026-1-5"
        assert format_time_modules(["时", ":", "分"], d, False) == "9:8"

    def test_year_always_four_digits(self) -> None:
        assert format_time_modules(["年"], datetime(999, 1, 1), False) == "0999"

    def test_space_module(self) -> None:
        assert format_time_modules(["年", "空格", "月"], datetime(2026, 10, 8)) == "2026 10"

    def test_slash_and_dot(self) -> None:
        d = datetime(2026, 10, 8)
        assert format_time_modules(["年", "/", "月", "/", "日"], d) == "2026/10/08"
        assert format_time_modules(["年", ".", "月"], d) == "2026.10"

    def test_repeated_module(self) -> None:
        assert format_time_modules(["年", "年"], datetime(2026, 10, 8)) == "20262026"

    def test_unknown_module_literal(self) -> None:
        assert format_time_modules(["年", "Q", "月"], datetime(2026, 10, 8)) == "2026Q10"


class TestShiftTime:
    def test_days(self) -> None:
        d = datetime(2026, 10, 8)
        assert shift_time(d, 3, "day").day == 11
        assert shift_time(d, -1, "day").day == 7

    def test_months_across_year(self) -> None:
        assert shift_time(datetime(2026, 10, 8), -1, "month").month == 9
        assert shift_time(datetime(2026, 1, 8), -1, "month").month == 12
        assert shift_time(datetime(2026, 1, 8), -1, "month").year == 2025
        assert shift_time(datetime(2026, 12, 8), 1, "month").month == 1
        assert shift_time(datetime(2026, 12, 8), 1, "month").year == 2027

    def test_month_end_clamped(self) -> None:
        """3-31 减 1 月 -> 2-28（不溢出到 3 月）；闰年 -> 2-29。"""
        assert shift_time(datetime(2026, 3, 31), -1, "month").day == 28
        assert shift_time(datetime(2028, 3, 31), -1, "month").day == 29

    def test_zero_offset(self) -> None:
        d = datetime(2026, 10, 8)
        assert shift_time(d, 0, "month") == d


# --------------------------------------------------------------------------
# calc_engine 分支
# --------------------------------------------------------------------------
class TestComputeTime:
    @pytest.mark.anyio
    async def test_text_shape_with_offset(self) -> None:
        m = make_metric(["年", "-", "月"], offset_value=-1, offset_unit="month")
        fact = await calc_engine._compute_time(m, "2026-09", now=datetime(2026, 10, 8, 14, 30))
        assert fact.value_shape == "text"
        assert fact.value == {"text": "2026-09"}
        assert fact.source == "当前时间"
        # formula 描述口径（偏移 + 格式模式），不含渲染结果
        assert "now(-1月)" in fact.formula
        assert "年-月" in fact.formula
        assert "基准=计算时刻(2026-10-08 14:30:00)" in fact.object

    @pytest.mark.anyio
    async def test_no_offset_uses_now(self) -> None:
        m = make_metric(["年", "-", "月", "-", "日", "空格", "时", ":", "分"])
        fact = await calc_engine._compute_time(m, "2026-09", now=datetime(2026, 10, 8, 9, 5))
        assert fact.value == {"text": "2026-10-08 09:05"}

    @pytest.mark.anyio
    async def test_dispatch_via_compute_metric(self) -> None:
        """time 分支不触碰 adapter（adapter 传 None）。"""
        m = make_metric(["年", "-", "月"], offset_value=0)
        fact = await compute_metric(m, "2026-09", None, "当前时间")
        assert fact.value_shape == "text"

    @pytest.mark.anyio
    async def test_invalid_config_raises(self) -> None:
        m = make_metric()
        m.type_config = {"time_config": {"format_modules": ["年", "Q"]}}
        with pytest.raises(MetricComputeError, match="time_config 非法"):
            await calc_engine._compute_time(m, "2026-09")

    @pytest.mark.anyio
    async def test_formula_mentions_offset(self) -> None:
        m = make_metric(["年", "-", "月"], offset_value=3, offset_unit="day")
        fact = await calc_engine._compute_time(m, "2026-09", now=datetime(2026, 10, 8))
        assert fact.formula.startswith("now(+3天)")


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------
ADAPTER = TypeAdapter(MetricCreate)


class TestSchema:
    def test_time_without_datasource_ok(self) -> None:
        m = ADAPTER.validate_python({"name": "now", "metric_type": "time"})
        assert m.data_source_id is None
        # query_config 允许为空（时间类型不取数）
        assert m.query_config.model_dump(exclude_none=True) in ({}, {"host_group_ids": [], "host_ids": []})
        assert m.type_config.time_config.format_modules == ["年", "-", "月"]

    @pytest.mark.parametrize(
        "payload",
        [
            {
                "name": "x",
                "metric_type": "scalar",
                "query_config": {"host_group_ids": ["4"], "host_ids": ["1"], "item_ids": ["2"]},
            },
            {
                "name": "c",
                "metric_type": "curve",
                "query_config": {"host_group_ids": ["4"], "host_ids": ["1"], "series": [{"item_id": "1"}]},
            },
            {
                "name": "t",
                "metric_type": "text",
                "query_config": {"host_group_ids": ["4"], "host_ids": ["1"]},
            },
        ],
    )
    def test_non_time_requires_datasource(self, payload: dict) -> None:
        with pytest.raises(ValidationError, match="data_source_id"):
            ADAPTER.validate_python(payload)

    def test_illegal_module_rejected(self) -> None:
        with pytest.raises(ValidationError, match="不支持的时间模块"):
            ADAPTER.validate_python(
                {
                    "name": "now",
                    "metric_type": "time",
                    "type_config": {"time_config": {"format_modules": ["年", "Q"]}},
                }
            )

    def test_empty_modules_rejected(self) -> None:
        with pytest.raises(ValidationError, match="不能为空"):
            ADAPTER.validate_python(
                {
                    "name": "now",
                    "metric_type": "time",
                    "type_config": {"time_config": {"format_modules": []}},
                }
            )

    def test_offset_limit(self) -> None:
        with pytest.raises(ValidationError, match="1000"):
            ADAPTER.validate_python(
                {
                    "name": "now",
                    "metric_type": "time",
                    "type_config": {"time_config": {"offset_value": 99999}},
                }
            )

    def test_time_ignores_scope_requirement(self) -> None:
        """时间类型不需要主机组/主机/监控项。"""
        m = ADAPTER.validate_python(
            {
                "name": "now",
                "metric_type": "time",
                "query_config": {"host_group_ids": [], "host_ids": []},
            }
        )
        assert m.metric_type == "time"


@pytest_asyncio.fixture
async def session_factory(tmp_path, monkeypatch):
    """内存库 + 建表（用于验证 time 指标可无数据源落库）。"""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


class TestPersistence:
    @pytest.mark.anyio
    async def test_roundtrip_in_db(self, session_factory) -> None:
        """time 指标可无数据源落库并读回（data_source_id 允许 NULL）。"""
        m = make_metric(["年", "-", "月"], metric_id=9101)
        async with session_factory() as s:
            s.add(m)
            await s.commit()
        async with session_factory() as s:
            got = await s.scalar(
                select(MetricDefinition).where(MetricDefinition.id == 9101)
            )
        assert got is not None
        assert got.data_source_id is None
        assert got.metric_type == "time"
        assert got.type_config["time_config"]["format_modules"] == ["年", "-", "月"]


# --------------------------------------------------------------------------
# 快速偏移（与前端 time.spec.ts 同批基准日）
# --------------------------------------------------------------------------
def ymd(dt) -> str:
    return format_time_modules(["年", "-", "月", "-", "日"], dt)


class TestQuickOffset:
    def test_base_friday(self) -> None:
        base = datetime(2026, 10, 9, 10, 0)  # 周五
        assert ymd(quick_target_time(base, "last_week_monday")) == "2026-09-28"
        assert ymd(quick_target_time(base, "last_week_sunday")) == "2026-10-04"
        assert ymd(quick_target_time(base, "last_month_first")) == "2026-09-01"
        assert ymd(quick_target_time(base, "last_month_end")) == "2026-09-30"

    def test_base_monday(self) -> None:
        base = datetime(2026, 10, 5, 10, 0)
        assert ymd(quick_target_time(base, "last_week_monday")) == "2026-09-28"
        assert ymd(quick_target_time(base, "last_week_sunday")) == "2026-10-04"

    def test_base_sunday(self) -> None:
        base = datetime(2026, 10, 11, 10, 0)
        assert ymd(quick_target_time(base, "last_week_monday")) == "2026-09-28"
        assert ymd(quick_target_time(base, "last_week_sunday")) == "2026-10-04"

    def test_cross_year(self) -> None:
        base = datetime(2026, 1, 31, 10, 0)
        assert ymd(quick_target_time(base, "last_month_first")) == "2025-12-01"
        assert ymd(quick_target_time(base, "last_month_end")) == "2025-12-31"

    def test_february_end(self) -> None:
        base = datetime(2026, 3, 1, 10, 0)
        assert ymd(quick_target_time(base, "last_month_end")) == "2026-02-28"

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(MetricComputeError, match="quick_offset"):
            quick_target_time(datetime(2026, 10, 9), "nope")

    def test_resolve_dispatch(self) -> None:
        base = datetime(2026, 10, 9, 10, 0)
        manual = resolve_time_target(
            base, TimeConfig(offset_mode="manual", offset_value=-1, offset_unit="month")
        )
        quick = resolve_time_target(
            base, TimeConfig(offset_mode="quick", quick_offset="last_month_first")
        )
        assert format_time_modules(["年", "-", "月"], manual) == "2026-09"
        assert format_time_modules(["年", "-", "月"], quick) == "2026-09"


class TestOffsetModeSchema:
    def test_default_is_manual(self) -> None:
        c = TimeConfig()
        assert c.offset_mode == "manual"
        assert c.quick_offset is None
        assert c.label() == "不偏移"

    def test_manual_label(self) -> None:
        assert TimeConfig(offset_value=-1, offset_unit="month").label() == "-1月"
        assert TimeConfig(offset_value=3, offset_unit="day").label() == "+3天"

    def test_quick_requires_quick_offset(self) -> None:
        with pytest.raises(ValidationError, match="quick_offset 必填"):
            TimeConfig(offset_mode="quick")

    def test_quick_invalid_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TimeConfig.model_validate({"offset_mode": "quick", "quick_offset": "yesterday"})

    def test_quick_label_is_chinese(self) -> None:
        assert TimeConfig(offset_mode="quick", quick_offset="last_week_monday").label() == "上周周一"
        assert TimeConfig(offset_mode="quick", quick_offset="last_month_end").label() == "上月月末"

    def test_quick_ignores_offset_value_limit(self) -> None:
        """quick 模式不校验 offset_value（它不参与计算）。"""
        TimeConfig(offset_mode="quick", quick_offset="last_month_first", offset_value=99999)

    def test_create_with_quick(self) -> None:
        m = ADAPTER.validate_python(
            {
                "name": "last_month_first",
                "metric_type": "time",
                "type_config": {
                    "time_config": {
                        "format_modules": ["年", "-", "月", "-", "日"],
                        "offset_mode": "quick",
                        "quick_offset": "last_month_first",
                    }
                },
            }
        )
        assert m.type_config.time_config.quick_offset == "last_month_first"


class TestComputeQuick:
    @pytest.mark.anyio
    async def test_quick_offset_text(self) -> None:
        m = make_metric(["年", "-", "月", "-", "日"])
        m.type_config = {
            "time_config": {
                "format_modules": ["年", "-", "月", "-", "日"],
                "offset_mode": "quick",
                "quick_offset": "last_month_first",
            }
        }
        fact = await calc_engine._compute_time(m, "2026-09", now=datetime(2026, 10, 9, 10, 0))
        assert fact.value == {"text": "2026-09-01"}
        assert "上月一号" in fact.formula
        assert "上月一号" in fact.object

    @pytest.mark.anyio
    async def test_manual_still_works(self) -> None:
        """切回手动偏移仍按 offset_value/unit。"""
        m = make_metric(["年", "-", "月"], offset_value=-1, offset_unit="month")
        m.type_config["time_config"]["offset_mode"] = "manual"
        fact = await calc_engine._compute_time(m, "2026-09", now=datetime(2026, 10, 9, 10, 0))
        assert fact.value == {"text": "2026-09"}
        assert "now(-1月)" in fact.formula
