"""period_utils 单元测试：UTC 边界与日历月平移。"""

from datetime import datetime, timezone

import pytest

from app.services.period_utils import (
    baseline_range,
    granularity_of,
    is_week,
    parse_period,
    parse_week,
    period_label,
    period_of_moment,
    period_to_unix_range,
    report_kind,
    shift_period,
    site_tz,
)


def ts(year: int, month: int) -> int:
    return int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())


class TestParsePeriod:
    def test_valid(self) -> None:
        assert parse_period("2026-09") == (2026, 9)

    @pytest.mark.parametrize("bad", ["2026-13", "2026-00", "2026-9", "abcd", "2026/09", ""])
    def test_invalid(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_period(bad)


class TestPeriodToUnixRange:
    def test_normal_month(self) -> None:
        assert period_to_unix_range("2026-09") == (ts(2026, 9), ts(2026, 10))

    def test_december_crosses_year(self) -> None:
        assert period_to_unix_range("2026-12") == (ts(2026, 12), ts(2027, 1))

    def test_leap_year_february(self) -> None:
        # 2024 闰年 2 月 29 天，end 必须是 3 月 1 日，不错位
        start, end = period_to_unix_range("2024-02")
        assert (start, end) == (ts(2024, 2), ts(2024, 3))
        assert (end - start) == 29 * 86400

    def test_common_year_february(self) -> None:
        start, end = period_to_unix_range("2026-02")
        assert (end - start) == 28 * 86400

    def test_31_day_month(self) -> None:
        start, end = period_to_unix_range("2026-01")
        assert (end - start) == 31 * 86400


class TestShiftPeriod:
    def test_backward(self) -> None:
        assert shift_period("2026-09", -1) == "2026-08"

    def test_cross_year_backward(self) -> None:
        assert shift_period("2026-01", -1) == "2025-12"

    def test_cross_year_forward(self) -> None:
        assert shift_period("2026-12", 1) == "2027-01"

    def test_yoy(self) -> None:
        assert shift_period("2026-09", -12) == "2025-09"


class TestBaselineRange:
    def test_mom(self) -> None:
        assert baseline_range("2026-09", 1) == (ts(2026, 8), ts(2026, 9))

    def test_cross_year_baseline(self) -> None:
        # 跨年基线：period=2026-01, mom, lookback=1 -> 2025-12 整月
        assert baseline_range("2026-01", 1) == (ts(2025, 12), ts(2026, 1))

    def test_yoy_baseline(self) -> None:
        assert baseline_range("2026-09", 12) == (ts(2025, 9), ts(2025, 10))

    def test_baseline_is_whole_calendar_month(self) -> None:
        # 基线区间长度 = 基线月自身天数（2026-02 平年 28 天）
        start, end = baseline_range("2026-03", 1)
        assert (start, end) == (ts(2026, 2), ts(2026, 3))
        assert (end - start) == 28 * 86400

    def test_invalid_lookback(self) -> None:
        with pytest.raises(ValueError):
            baseline_range("2026-09", 0)


# ---------------------------------------------------------------------------
# 周粒度（ISO 周）：与月粒度共用同一组函数，按编码自动分派
# ---------------------------------------------------------------------------
def week_ts(year: int, week: int) -> int:
    """站点时区下该周周一的 Unix 秒（测试内推导，避免硬编码时区偏移）。"""
    monday = datetime.fromisocalendar(year, week, 1).replace(tzinfo=site_tz())
    return int(monday.timestamp())


def local_date(unix_ts: int) -> str:
    return datetime.fromtimestamp(unix_ts, tz=site_tz()).strftime("%Y-%m-%d")


class TestPeriodGranularity:
    def test_is_week(self) -> None:
        assert is_week("2026-W41") is True
        assert is_week("2026-09") is False
        assert is_week("2026-41") is False

    def test_granularity_of(self) -> None:
        assert granularity_of("2026-09") == "month"
        assert granularity_of("2026-W41") == "week"

    @pytest.mark.parametrize("bad", ["2026-", "2026-W", "26-W41", "2026-Wx", ""])
    def test_granularity_invalid(self, bad: str) -> None:
        with pytest.raises(ValueError):
            granularity_of(bad)


class TestParseWeek:
    def test_valid(self) -> None:
        assert parse_week("2026-W41") == (2026, 41)

    @pytest.mark.parametrize(
        "bad", ["2026-41", "2026-W1", "2026-W00", "2026-W54", "abcd"]
    )
    def test_invalid(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_week(bad)

    def test_month_parser_stays_month_only(self) -> None:
        # 兼容性护栏：parse_period 仍是月专用，返回值语义不变
        with pytest.raises(ValueError):
            parse_period("2026-W41")


class TestWeekRange:
    def test_normal_week(self) -> None:
        assert period_to_unix_range("2026-W41") == (
            week_ts(2026, 41),
            week_ts(2026, 42),
        )

    def test_week_is_exactly_7_days(self) -> None:
        start, end = period_to_unix_range("2026-W41")
        assert (end - start) == 7 * 86400

    def test_starts_on_monday(self) -> None:
        # 2026-W41 覆盖 10/05~10/11：起点恒为周一，不随触发日漂移
        start, _ = period_to_unix_range("2026-W41")
        assert local_date(start) == "2026-10-05"

    def test_iso_year_boundary(self) -> None:
        # ISO 年与日历年不一致的经典场景：2026-W01 的周一落在 2025-12-29
        start, _ = period_to_unix_range("2026-W01")
        assert local_date(start) == "2025-12-29"


class TestWeekShift:
    def test_backward(self) -> None:
        assert shift_period("2026-W41", -1) == "2026-W40"

    def test_forward(self) -> None:
        assert shift_period("2026-W41", 1) == "2026-W42"

    def test_cross_iso_year_backward(self) -> None:
        assert shift_period("2026-W01", -1) == "2025-W52"

    def test_cross_iso_year_forward(self) -> None:
        assert shift_period("2025-W52", 1) == "2026-W01"

    def test_full_year_shift(self) -> None:
        assert shift_period("2026-W41", -52) == "2025-W41"


class TestWeekBaseline:
    def test_prev_week(self) -> None:
        assert baseline_range("2026-W41", 1) == (week_ts(2026, 40), week_ts(2026, 41))

    def test_lookback_is_whole_week(self) -> None:
        start, end = baseline_range("2026-W41", 4)
        assert (start, end) == (week_ts(2026, 37), week_ts(2026, 38))
        assert (end - start) == 7 * 86400

    def test_cross_iso_year_baseline(self) -> None:
        assert baseline_range("2026-W01", 1) == (week_ts(2025, 52), week_ts(2026, 1))

    def test_invalid_lookback(self) -> None:
        with pytest.raises(ValueError):
            baseline_range("2026-W41", 0)


class TestPeriodLabel:
    def test_month_label_is_raw(self) -> None:
        assert period_label("2026-09") == "2026-09"

    def test_week_label(self) -> None:
        assert period_label("2026-W41") == "2026年第41周（10/05–10/11）"


class TestReportKind:
    def test_kind(self) -> None:
        assert report_kind("2026-09") == "月报"
        assert report_kind("2026-W41") == "周报"


class TestPeriodOfMoment:
    def test_month(self) -> None:
        moment = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
        assert period_of_moment(moment) == "2026-10"
        assert period_of_moment(moment, "month", -1) == "2026-09"

    def test_week(self) -> None:
        moment = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        assert period_of_moment(moment, "week") == "2026-W41"
        assert period_of_moment(moment, "week", -1) == "2026-W40"

    def test_granularity_is_derivable(self) -> None:
        moment = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        assert granularity_of(period_of_moment(moment, "week")) == "week"
