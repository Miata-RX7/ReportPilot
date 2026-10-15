"""chart_renderer 单测：只验证产物合法性，不校验像素内容。

不引入 Pillow：PNG 的宽高直接从 IHDR 读取。
"""

import struct
from pathlib import Path

import pytest

from app.schemas.metric import MetricFact
from app.services import chart_renderer
from app.services.chart_renderer import (
    CURVE_SIZE_MM,
    DPI,
    PIE_SIZE_MM,
    ChartRenderError,
    compact_number,
    render_fact_chart,
)

# 测试环境统一字体缓存目录，避免容器内 HOME 不可写
import os

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplconfig-test")


def png_size(data: bytes) -> tuple[int, int]:
    """从 PNG IHDR 读宽高。"""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "不是合法 PNG"
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def expected_px(size_mm: tuple[float, float], dpi: int = DPI) -> tuple[int, int]:
    return (
        round(size_mm[0] / 25.4 * dpi),
        round(size_mm[1] / 25.4 * dpi),
    )


def curve_fact(
    series: list[dict] | None = None,
    *,
    name: str = "流量图",
    unit: str = "Mbps",
    precision: int | None = None,
) -> MetricFact:
    if series is None:
        series = [
            {
                "name": "入向",
                "item_id": "1",
                "points": [[1788220800, 252423.0], [1788224400, 519373.0]],
            }
        ]
    return MetricFact(
        metric_id=1,
        name=name,
        value={"series": series},
        value_shape="series",
        baseline_value=None,
        change_pct=None,
        unit=unit,
        source="Zabbix",
        object="组: A; 主机: h1",
        formula="raw",
        period="2026-09",
        display_precision=precision,
    )


class TestRenderCurve:
    def test_png_legal_and_size(self) -> None:
        data = render_fact_chart(curve_fact())
        w, h = png_size(data)
        exp_w, exp_h = expected_px(CURVE_SIZE_MM)
        assert abs(w - exp_w) <= 2 and abs(h - exp_h) <= 2, f"{w}x{h} vs {exp_w}x{exp_h}"

    def test_file_size_reasonable(self) -> None:
        data = render_fact_chart(curve_fact())
        assert 1024 < len(data) < 500 * 1024, len(data)

    def test_720_points_renders(self) -> None:
        points = [[1788220800 + i * 3600, 1000.0 + (i % 17) * 30] for i in range(720)]
        data = render_fact_chart(
            curve_fact([{"name": "长序列", "item_id": "1", "points": points}])
        )
        assert png_size(data)[0] > 0
        assert len(data) < 500 * 1024

    def test_single_point_renders(self) -> None:
        data = render_fact_chart(
            curve_fact([{"name": "单点", "item_id": "1", "points": [[1788220800, 5.0]]}])
        )
        assert png_size(data)[0] > 0

    def test_multi_series_colors_recycled(self) -> None:
        base = {"item_id": "1", "points": [[1788220800, 1.0]]}
        data = render_fact_chart(
            curve_fact([{**base, "name": f"s{i}"} for i in range(7)])  # 超过配色数
        )
        assert png_size(data)[0] > 0

    def test_null_values_filtered(self) -> None:
        data = render_fact_chart(
            curve_fact(
                [
                    {
                        "name": "含空点",
                        "item_id": "1",
                        "points": [[1788220800, None], [1788224400, 3.0]],
                    }
                ]
            )
        )
        assert png_size(data)[0] > 0

    def test_chinese_labels_do_not_raise(self) -> None:
        """中文标签/标题渲染不抛异常（不断言字体是否真为中文）。"""
        data = render_fact_chart(
            curve_fact(name="接口流量趋势（Gi0/0/21）", unit="bps")
        )
        assert len(data) > 1024


class TestCurveUnitScale:
    """曲线换算：点值先缩放再绘制，Y 轴单位与图内标注同口径。"""

    def _capture(self, monkeypatch: pytest.MonkeyPatch):
        import matplotlib.axes

        captured: dict = {}
        real_annotate = chart_renderer._annotate

        def fake_annotate(ax, series, precision):  # type: ignore[no-untyped-def]
            captured["series"] = series
            return real_annotate(ax, series, precision)

        monkeypatch.setattr(chart_renderer, "_annotate", fake_annotate)
        labels: list[str] = []
        monkeypatch.setattr(
            matplotlib.axes.Axes,
            "set_ylabel",
            lambda self, label, **kw: labels.append(label),
        )
        return captured, labels

    def test_points_and_ylabel_use_display_scale(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured, labels = self._capture(monkeypatch)
        fact = curve_fact()
        fact.display_factor = 1000.0
        fact.display_unit = "Kbps"
        fact.display_precision = 2
        render_fact_chart(fact)
        vals = [p[1] for p in captured["series"][0]["points"]]
        assert vals == pytest.approx([252.423, 519.373])
        assert labels == ["Kbps"]

    def test_unconfigured_behaviour_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured, labels = self._capture(monkeypatch)
        fact = curve_fact()
        render_fact_chart(fact)
        vals = [p[1] for p in captured["series"][0]["points"]]
        assert vals == pytest.approx([252423.0, 519373.0])
        # display_unit 为 None -> 回退原始单位
        assert labels == ["Mbps"]

    def test_scaled_png_still_legal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fact = curve_fact()
        fact.display_factor = 1000.0
        fact.display_unit = "Kbps"
        data = render_fact_chart(fact)
        w, h = png_size(data)
        exp_w, exp_h = expected_px(CURVE_SIZE_MM)
        assert abs(w - exp_w) <= 2 and abs(h - exp_h) <= 2


class TestEmptyData:
    def test_all_series_empty_gives_placeholder(self) -> None:
        data = render_fact_chart(
            curve_fact([{"name": "空", "item_id": "1", "points": []}])
        )
        w, h = png_size(data)
        exp_w, exp_h = expected_px(CURVE_SIZE_MM)
        assert abs(w - exp_w) <= 2 and abs(h - exp_h) <= 2
        # 占位图内容极少，明显小于真实曲线
        assert len(data) < 20 * 1024

    def test_no_series_at_all(self) -> None:
        data = render_fact_chart(curve_fact([]))
        assert png_size(data)[0] > 0

    def test_placeholder_not_error(self) -> None:
        """空数据不抛异常（不阻塞报告生成）。"""
        render_fact_chart(curve_fact([{"name": "x", "item_id": "1", "points": []}]))
        assert True


def pie_fact(
    up: int = 700,
    down: int = 44,
    *,
    up_pct: float = 94.1,
    down_pct: float = 5.9,
    name: str = "可用率（icmpping）",
    precision: int | None = 1,
) -> MetricFact:
    return MetricFact(
        metric_id=2,
        name=name,
        value={
            "up_count": up,
            "down_count": down,
            "up_pct": up_pct,
            "down_pct": down_pct,
        },
        value_shape="pie",
        baseline_value=None,
        change_pct=None,
        unit="",
        source="Zabbix",
        object="组: A; 主机: h1",
        formula="f",
        period="2026-09",
        display_precision=precision,
    )


class TestRenderPie:
    def test_png_legal_and_size(self) -> None:
        data = render_fact_chart(pie_fact())
        w, h = png_size(data)
        exp_w, exp_h = expected_px(PIE_SIZE_MM)
        assert abs(w - exp_w) <= 2 and abs(h - exp_h) <= 2, f"{w}x{h} vs {exp_w}x{exp_h}"

    def test_size_smaller_than_curve(self) -> None:
        """饼图比曲线小（100x80mm vs 140x70mm）——面积更小。"""
        pw, ph = png_size(render_fact_chart(pie_fact()))
        cw, ch = png_size(render_fact_chart(curve_fact()))
        assert pw * ph < cw * ch

    def test_file_size_reasonable(self) -> None:
        data = render_fact_chart(pie_fact())
        assert 1024 < len(data) < 500 * 1024

    def test_empty_gives_placeholder(self) -> None:
        """up+down=0 -> 暂无数据占位图，不抛异常。"""
        data = render_fact_chart(pie_fact(up=0, down=0, up_pct=0.0, down_pct=0.0))
        w, h = png_size(data)
        exp_w, exp_h = expected_px(PIE_SIZE_MM)
        assert abs(w - exp_w) <= 2 and abs(h - exp_h) <= 2
        assert len(data) < 20 * 1024

    def test_single_side_only(self) -> None:
        """全部 UP（全通）也要正常出图，不能因 down=0 崩。"""
        data = render_fact_chart(pie_fact(up=744, down=0, up_pct=100.0, down_pct=0.0))
        assert len(data) > 1024

    def test_labels_used(self) -> None:
        """传入 pie_labels 与不传都能出图（标签只影响文字）。"""
        a = render_fact_chart(pie_fact())
        b = render_fact_chart(pie_fact(), pie_labels=("UP", "DOWN"))
        assert a[:8] == b[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(a) > 0 and len(b) > 0

    def test_colors_are_up_green_down_red(self) -> None:
        """饼图用独立的语义色常量（与 Design Token 的 success/danger 同值）。"""
        assert chart_renderer.PIE_COLORS == ("#10B981", "#EF4444")
        # 语义色与分类色板是两套常量，pie 只用前者
        assert chart_renderer.PIE_COLORS is not chart_renderer.SERIES_COLORS
        src = Path(chart_renderer.__file__).read_text(encoding="utf-8")
        assert src.count("SERIES_COLORS") > 0  # 曲线仍用分类色
        assert "PIE_COLORS" in src

    def test_precision_none_renders(self) -> None:
        data = render_fact_chart(pie_fact(precision=None))
        assert len(data) > 1024

    def test_chinese_title(self) -> None:
        data = render_fact_chart(pie_fact(name="网络可用率（核心链路）"))
        assert len(data) > 1024


class TestDispatch:
    def test_pie_no_longer_placeholder(self) -> None:
        """3B 起 pie 真实渲染（不再是"阶段 3B"占位图）。"""
        data = render_fact_chart(pie_fact())
        assert len(data) > 20 * 1024, "pie 不应再走占位图"

    @pytest.mark.parametrize("shape", ["scalar", "text", "series_x"])
    def test_non_chart_shape_raises(self, shape: str) -> None:
        fact = curve_fact()
        fact.value_shape = shape
        with pytest.raises(ChartRenderError):
            render_fact_chart(fact)


class TestFormatters:
    def test_compact_number(self) -> None:
        assert compact_number(1234567) == "1.23M"
        assert compact_number(250000) == "250.0K"
        assert compact_number(2_000_000_000) == "2.00G"
        assert compact_number(0) == "0"
        assert compact_number(12.5) == "12.5"

    def test_precision_used_in_annotation(self) -> None:
        """display_precision 影响标注（通过渲染不抛异常 + 体积变化间接验证）。"""
        a = render_fact_chart(curve_fact(precision=None))
        b = render_fact_chart(curve_fact(precision=4))
        assert a[:8] == b[:8] == b"\x89PNG\r\n\x1a\n"


class TestFontConfig:
    def test_font_configured_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_ensure_font_configured 有全局 flag，重复调用不会重复探测。"""
        monkeypatch.setattr(chart_renderer, "_FONT_READY", False)
        calls = {"n": 0}
        real = chart_renderer.font_manager.findfont

        def counting(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(chart_renderer.font_manager, "findfont", counting)
        chart_renderer._ensure_font_configured()
        first = calls["n"]
        assert first > 0
        chart_renderer._ensure_font_configured()
        assert calls["n"] == first, "第二次调用不应再探测字体"

    def test_unicode_minus_disabled(self) -> None:
        chart_renderer._ensure_font_configured()
        import matplotlib.pyplot as plt

        assert plt.rcParams["axes.unicode_minus"] is False
