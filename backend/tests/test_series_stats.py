"""series_stats 单元测试：曲线摘要的唯一口径（Word 图内标注与 AI 输入共用）。"""

import pytest

from app.services.series_stats import EMPTY_STATS, stats_of


def pts(*values: float) -> list[list[float]]:
    """构造 [[unix_ts, value], ...]；时间戳只是陪衬，被统计的是值。"""
    return [[1700000000 + i * 3600, float(v)] for i, v in enumerate(values)]


class TestStatsOf:
    def test_basic_fields(self) -> None:
        st = stats_of(pts(10, 30, 20))
        assert st.count == 3
        assert st.min == 10.0
        assert st.max == 30.0
        assert st.avg == pytest.approx(20.0)
        assert st.last == 20.0

    def test_single_point(self) -> None:
        st = stats_of(pts(42))
        assert (st.count, st.min, st.max, st.avg, st.last) == (1, 42.0, 42.0, 42.0, 42.0)

    def test_negative_values(self) -> None:
        st = stats_of(pts(-5, 5))
        assert (st.min, st.max, st.last) == (-5.0, 5.0, 5.0)

    def test_empty_and_none(self) -> None:
        """没有有效点返回 EMPTY_STATS（全 None），而不是 0 —— 「无数据」≠「值为 0」。"""
        assert stats_of([]) == EMPTY_STATS
        assert stats_of(None) == EMPTY_STATS
        assert EMPTY_STATS.count == 0
        assert EMPTY_STATS.max is None

    def test_skips_missing_and_malformed_points(self) -> None:
        """缺值点 / 短点 / 非数值点都不参与统计，也不让脏数据抛错。"""
        raw = [[1, None], [2, 5.0], [3], [4, "x"], "junk", [5, True], [6, 15.0]]
        st = stats_of(raw)
        assert st.count == 2
        assert (st.min, st.max, st.avg, st.last) == (5.0, 15.0, 10.0, 15.0)

    def test_factor_scales_to_display(self) -> None:
        """factor 为展示换算的除数（与 ResolvedScale.apply 同向）。"""
        st = stats_of(pts(1000, 3000), factor=1000.0)
        assert (st.min, st.max, st.avg, st.last) == (1.0, 3.0, 2.0, 3.0)

    def test_invalid_factor_falls_back_to_raw(self) -> None:
        """脏配置（0 / 负数）按不换算处理，不能算出 inf。"""
        assert stats_of(pts(4), factor=0).max == 4.0
        assert stats_of(pts(4), factor=-2.0).max == 4.0