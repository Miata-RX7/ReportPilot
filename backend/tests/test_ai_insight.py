"""ai_insight 单元测试（respx mock OpenAI 兼容端点）。"""

import json

import httpx
import pytest
import respx

from app.config import get_settings
from app.schemas.metric import MetricFact
from app.services.ai_insight import (
    MAX_SUMMARY_CHARS,
    MAX_SUMMARY_CHARS_LIMIT,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_USER_INPUT,
    InsightConfig,
    build_correction,
    build_messages,
    build_payload,
    forbidden_numbers,
    generate_summary,
    item_count,
    render_system_prompt,
    resolve_system_prompt,
    sample_item_overrun,
    sample_leaks,
    truncate_by_sentence,
    verify_numbers,
)
from app.services.preview_service import AI_SUMMARY_PLACEHOLDER

AI_BASE = "http://ai.test/v1"
CHAT_URL = f"{AI_BASE}/chat/completions"


def make_fact(**kw) -> MetricFact:
    base = {
        "metric_id": 1,
        "name": "cpu_usage",
        "value": 35.0,
        "baseline_value": 30.0,
        "change_pct": 16.67,
        "unit": "%",
        "source": "生产 Zabbix",
        "object": "Linux servers",
        "formula": "avg(...)",
        "period": "2026-09",
    }
    base.update(kw)
    return MetricFact(**base)


def make_curve_fact(series: list[dict] | None = None, **kw) -> MetricFact:
    """曲线 fact（value_shape=series），形状与 calc_engine 产出一致。"""
    kw.setdefault("unit", "bps")
    kw.setdefault("baseline_value", None)
    kw.setdefault("change_pct", None)
    if series is None:
        series = [
            {
                "name": "Gi0/0/21 出方向",
                "points": [[1700000000 + i * 3600, float(v)] for i, v in enumerate([100, 300, 200])],
            }
        ]
    return make_fact(value={"series": series}, value_shape="series", **kw)


@pytest.fixture
def ai_settings(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_base_url", AI_BASE)
    monkeypatch.setattr(settings, "ai_api_key", "test-ai-key")
    monkeypatch.setattr(settings, "ai_model", "test-model")
    return settings


def chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
    )


class TestVerifyNumbers:
    def test_exact_and_rounded_hits(self) -> None:
        facts = [make_fact()]
        assert verify_numbers("CPU 使用率 35，环比上升 16.67%。", facts) == []

    def test_abs_tolerance_for_large_values(self) -> None:
        facts = [make_fact(value=35.0)]
        # |actual| >= 1：绝对容差 0.005
        assert verify_numbers("值 35.004", facts) == []
        assert verify_numbers("值 35.01", facts) == ["35.01"]

    def test_rel_tolerance_for_small_values(self) -> None:
        facts = [make_fact(change_pct=0.04)]
        # |actual| < 1：相对容差 0.04 * 0.05 = 0.002
        assert verify_numbers("变化 0.041", facts) == []
        assert verify_numbers("变化 0.05", facts) == ["0.05"]

    def test_percent_sign_stripped(self) -> None:
        facts = [make_fact(change_pct=16.67)]
        assert verify_numbers("上升 16.67%", facts) == []

    def test_unverified_deduplicated(self) -> None:
        facts = [make_fact()]
        assert verify_numbers("99 和 99 都不是输入数据", facts) == ["99"]

    def test_digits_in_fact_name_excluded(self) -> None:
        """指标名中的数字片段（gi0_0_21）不参与未验证判定。"""
        facts = [
            make_fact(
                name="gi0_0_21_outbound_peak",
                object="爱迅达办公室（1 台主机, 1 个监控项）",
                value=276170408.0,
                baseline_value=218156600.0,
                change_pct=26.5927,
            ),
            make_fact(
                metric_id=2,
                name="gi0_0_21_inbound_avg",
                value=231421.958,
                baseline_value=235963.08,
                change_pct=-1.9245,
            ),
        ]
        text = (
            "本期 gi0_0_21_outbound_peak 为 276170408 bps，环比上升 26.59%，"
            "波动显著。gi0_0_21_inbound_avg 为 231421.96 bps，环比下降 1.92%，波动平稳。"
        )
        assert verify_numbers(text, facts) == []

    def test_digits_in_object_excluded_but_fabricated_kept(self) -> None:
        """Gi0/0/21 这类对象描述中的数字被排除，编造数值仍被捕获。"""
        facts = [
            make_fact(
                name="gi_in",
                object="Gi0/0/21 Bits received（1 台主机）",
                value=231421.958,
                baseline_value=235963.08,
                change_pct=-1.9245,
            )
        ]
        assert verify_numbers("Gi0/0/21 入向下降 1.92%", facts) == []
        assert verify_numbers("Gi0/0/21 环比上升 5.55%", facts) == ["5.55%"]

    def test_digit_fragments_do_not_hide_real_numbers(self) -> None:
        """排除集只影响上下文数字片段，不影响对事实数值的判定。"""
        facts = [make_fact(name="metric_2026_09", object="host-01", value=35.0)]
        assert verify_numbers("指标 metric_2026_09 的值 35", facts) == []
        assert verify_numbers("指标 metric_2026_09 的值 42", facts) == ["42"]

    def test_none_fields_ignored(self) -> None:
        facts = [make_fact(baseline_value=None, change_pct=None)]
        assert verify_numbers("值 35", facts) == []
        assert verify_numbers("值 30", facts) == ["30"]


class TestDateMasking:
    """期间/日期是上下文标识，不是指标数值 —— 不得触发溯源误报或禁抄误判。"""

    def test_period_not_reported_as_unverified(self) -> None:
        """回归：2026-09 会被 _NUMBER_RE 切成 2026 与 -09，曾报出「-09 未溯源」。"""
        facts = [make_fact(period="2026-09")]
        assert verify_numbers("统计周期为 2026-09，本期各项指标波动平稳。", facts) == []
        assert verify_numbers("统计周期为 2026年9月，本期平稳。", facts) == []
        assert verify_numbers("统计周期为 2026/09，本期平稳。", facts) == []

    def test_real_numbers_still_caught_after_masking(self) -> None:
        facts = [make_fact(value=35.0, baseline_value=None, change_pct=None)]
        assert verify_numbers("2026-09 本期为 88", facts) == ["88"]

    def test_sample_month_not_forbidden(self) -> None:
        """范文里的 2026年9月 曾被切成 "9" 进禁抄清单，导致无谓的纠正重试。"""
        facts = [make_fact()]
        assert forbidden_numbers("2026年9月各项指标平稳。", facts) == set()
        # 范文里真正不属于本期事实的数值仍要禁抄
        assert forbidden_numbers("2026年9月，CPU 使用率 88%。", facts) == {"88"}

    def test_sample_leaks_ignores_period(self) -> None:
        facts = [make_fact()]
        forbidden = forbidden_numbers("2026年9月 CPU 使用率 88%。", facts)
        assert forbidden == {"88"}
        assert sample_leaks("2026-09 本期 CPU 达到 88%。", forbidden) == ["88"]
        assert sample_leaks("2026-09 本期指标平稳。", forbidden) == []


class TestDisplayScope:
    """数字自检与 AI 输入都必须走「展示口径」（换算后），与报告正文一致。"""

    def test_display_value_preferred(self) -> None:
        facts = [
            make_fact(
                name="gi_out",
                value=262468.9041666667,
                baseline_value=118000.0,
                change_pct=None,
                unit="bps",
                display_value=262.4689041666667,
                display_baseline_value=118.0,
                display_unit="Kbps",
                display_factor=1000.0,
                display_precision=2,
            )
        ]
        assert verify_numbers("入向流量 262.47 Kbps，基线 118.00 Kbps。", facts) == []

    def test_raw_long_string_no_longer_traces(self) -> None:
        """AI 引用原始长串不再通过自检（报告展示的是 262.47，口径必须同一套）。"""
        facts = [
            make_fact(
                value=262468.9041666667,
                baseline_value=None,
                change_pct=None,
                unit="bps",
                display_value=262.4689041666667,
                display_unit="Kbps",
                display_factor=1000.0,
                display_precision=2,
            )
        ]
        assert verify_numbers("入向 262468.9 bps", facts) == ["262468.9"]

    def test_rounding_tolerance_from_display_precision(self) -> None:
        """展示 1 位小数时，落在 0.5×10⁻¹ 半宽内视为合理舍入，不算编造。"""
        facts = [
            make_fact(
                value=100.0,
                baseline_value=None,
                change_pct=None,
                display_value=100.0,
                display_unit="Kbps",
                display_factor=1.0,
                display_precision=1,
            )
        ]
        assert verify_numbers("值 100.04", facts) == []
        assert verify_numbers("值 100.06", facts) == ["100.06"]

    def test_build_payload_uses_display(self) -> None:
        facts = [
            make_fact(
                value=262468.9041666667,
                baseline_value=118000.0,
                change_pct=122.4,
                unit="bps",
                display_value=262.4689041666667,
                display_baseline_value=118.0,
                display_unit="Kbps",
                display_factor=1000.0,
            )
        ]
        payload = build_payload(facts)[0]
        assert payload["value"] == pytest.approx(262.4689041666667)
        assert payload["unit"] == "Kbps"
        assert payload["baseline_value"] == pytest.approx(118.0)
        # change_pct 是比值，不受换算影响
        assert payload["change_pct"] == 122.4

    def test_build_payload_falls_back_to_raw(self) -> None:
        """未配置换算（display_* = None）时行为与升级前一致。"""
        payload = build_payload([make_fact()])[0]
        assert payload["value"] == 35.0
        assert payload["unit"] == "%"
        assert payload["baseline_value"] == 30.0

    def test_build_payload_rounds_to_display_precision(self) -> None:
        """送进 prompt 的数就是报告正文展示的数（262.47，不是 262.4689041666667）。

        线上真实现象：payload 给长串，模型要么舍入要么照抄，重试版就抄成了
        262.4689041666667 Kbps。口径同一套之后这个长串根本不会出现在 prompt 里。
        """
        facts = [
            make_fact(
                value=262468.9041666667,
                baseline_value=118000.0,
                change_pct=None,
                unit="bps",
                display_value=262.4689041666667,
                display_baseline_value=118.0,
                display_unit="Kbps",
                display_factor=1000.0,
                display_precision=2,
            )
        ]
        payload = build_payload(facts)[0]
        assert payload["value"] == 262.47
        assert payload["baseline_value"] == 118.0
        # 取整后仍能通过自检（半宽容差按 display_precision 给）
        assert verify_numbers("入向 262.47 Kbps，基线 118.00 Kbps。", facts) == []

    def test_build_payload_keeps_raw_without_precision(self) -> None:
        """未配 display_precision 的指标不加工，避免旧报告口径漂移。"""
        payload = build_payload([make_fact(value=262468.9041666667)])[0]
        assert payload["value"] == pytest.approx(262468.9041666667)


class TestSeriesSummary:
    """曲线指标只送后端算好的摘要，原始点一个都不进 prompt。

    线上真实现象：`流量图` 的 3 万多个原始点全塞进 prompt（user message 34KB），
    模型据此自己「找出」了峰值，还照范文句式补了一条独立的指标数据行。
    """

    def test_raw_points_never_reach_the_prompt(self) -> None:
        points = [[1700000000 + i * 3600, 1000.0 + i] for i in range(720)]
        item = build_payload([make_curve_fact(series=[{"name": "流量图", "points": points}])])[0]
        raw = json.dumps(item, ensure_ascii=False)
        assert "points" not in raw
        # 720 个点原样序列化约 15KB，摘要必须在几百字节内
        assert len(json.dumps(points)) > 8000
        assert len(raw) < 400

    def test_summary_fields_and_unit(self) -> None:
        item = build_payload([make_curve_fact()])[0]
        assert item["unit"] == "bps"
        assert item["baseline_value"] is None
        assert item["change_pct"] is None
        s = item["value"]["series"][0]
        assert s["name"] == "Gi0/0/21 出方向"
        assert (s["min"], s["max"], s["avg"], s["last"]) == (100.0, 300.0, 200.0, 200.0)

    def test_multi_series_all_summarized(self) -> None:
        fact = make_curve_fact(
            series=[
                {"name": "入向", "points": [[1, 1.0]]},
                {"name": "出向", "points": [[1, 2.0]]},
            ]
        )
        series = build_payload([fact])[0]["value"]["series"]
        assert [s["name"] for s in series] == ["入向", "出向"]
        assert [s["max"] for s in series] == [1.0, 2.0]

    def test_empty_series_yields_nulls(self) -> None:
        """没有采样点 -> 全 null（不是 0），并且不参与自检、不抛错。"""
        fact = make_curve_fact(series=[{"name": "空", "points": []}])
        s = build_payload([fact])[0]["value"]["series"][0]
        assert (s["min"], s["max"], s["avg"], s["last"]) == (None, None, None, None)
        assert verify_numbers("本期无数据。", [fact]) == []

    def test_summary_uses_display_scale_and_precision(self) -> None:
        """摘要与报告正文同口径：÷display_factor 后按 display_precision 取整。"""
        fact = make_curve_fact(
            display_factor=1000.0,
            display_unit="Kbps",
            display_precision=2,
            series=[
                {"name": "入向", "points": [[1, 262468.9041666667], [2, 118000.0]]}
            ],
        )
        item = build_payload([fact])[0]
        assert item["unit"] == "Kbps"
        s = item["value"]["series"][0]
        assert s["max"] == 262.47
        assert (s["min"], s["last"]) == (118.0, 118.0)
        assert s["avg"] == 190.23

    def test_summary_numbers_are_traceable(self) -> None:
        """AI 引用峰值/均值必须通过自检，编一个不行。"""
        fact = make_curve_fact()
        assert verify_numbers("出方向峰值 300 bps，均值 200 bps。", [fact]) == []
        assert verify_numbers("出方向峰值 999 bps。", [fact]) == ["999"]

    def test_single_point_values_are_not_traceable(self) -> None:
        """只送摘要就不能再引用单个采样点（它已不在 prompt 里，引用即编造）。"""
        fact = make_curve_fact(
            series=[{"name": "入向", "points": [[1, 100.0], [2, 300.0], [3, 250.0], [4, 200.0]]}]
        )
        assert verify_numbers("峰值 300 bps，均值 212.5 bps。", [fact]) == []
        assert verify_numbers("某时刻 250 bps。", [fact]) == ["250"]

    def test_prompt_stays_small_with_huge_curve(self) -> None:
        """端到端：720 点曲线 + 标量，user message 必须是 KB 级而不是 34KB。"""
        points = [[1700000000 + i * 3600, 1e6 + i] for i in range(720)]
        facts = [make_curve_fact(series=[{"name": "流量图", "points": points}]), make_fact()]
        messages = build_messages(facts, "2026-09", InsightConfig(), 5.0)
        user = messages[1]["content"]
        assert "points" not in user
        assert "流量图" in user
        assert len(user) < 1500


class TestTruncate:
    def test_short_text_unchanged(self) -> None:
        assert truncate_by_sentence("短文本。") == "短文本。"

    def test_cut_at_last_sentence(self) -> None:
        text = "。" * 0 + "甲" * 100 + "。" + "乙" * 150
        result = truncate_by_sentence(text)
        assert result.endswith("。")
        assert len(result) <= MAX_SUMMARY_CHARS

    def test_hard_cut_without_punctuation(self) -> None:
        text = "甲" * 300
        result = truncate_by_sentence(text)
        assert len(result) == MAX_SUMMARY_CHARS


class TestGenerateSummary:
    @respx.mock
    async def test_success(self, ai_settings) -> None:
        route = respx.post(CHAT_URL).mock(
            return_value=chat_response("CPU 使用率 35%，环比上升 16.67%，建议关注。")
        )
        result = await generate_summary([make_fact()], "2026-09")
        assert result["degraded"] is False
        assert "35" in result["text"]

        sent = json.loads(route.calls.last.request.content)
        system = sent["messages"][0]["content"]
        user = sent["messages"][1]["content"]
        assert "只允许引用输入 JSON" in system
        assert "10%" in system  # ai_change_threshold 注入
        assert "cpu_usage" in user and "16.67" in user
        # 证据字段不进入 prompt
        assert "生产 Zabbix" not in user

    @respx.mock
    async def test_over_200_chars_truncated(self, ai_settings) -> None:
        long_text = "甲" * 100 + "。" + "乙" * 150 + "。"
        respx.post(CHAT_URL).mock(return_value=chat_response(long_text))
        result = await generate_summary([make_fact()], "2026-09")
        assert len(result["text"]) <= MAX_SUMMARY_CHARS
        assert result["text"].endswith("。")

    @respx.mock
    async def test_http_error_degrades(self, ai_settings) -> None:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(500))
        result = await generate_summary([make_fact()], "2026-09")
        assert result["degraded"] is True
        assert result["text"] == AI_SUMMARY_PLACEHOLDER

    @respx.mock
    async def test_timeout_degrades(self, ai_settings) -> None:
        respx.post(CHAT_URL).mock(side_effect=httpx.ReadTimeout("timeout"))
        result = await generate_summary([make_fact()], "2026-09")
        assert result["degraded"] is True

    async def test_no_api_key_degrades_without_http(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(get_settings(), "ai_api_key", "")
        result = await generate_summary([make_fact()], "2026-09")
        assert result["degraded"] is True
        assert result["text"] == AI_SUMMARY_PLACEHOLDER

    @respx.mock
    async def test_unverified_numbers_reported(self, ai_settings) -> None:
        respx.post(CHAT_URL).mock(
            return_value=chat_response("CPU 35，内存飙升到 88.8。")
        )
        result = await generate_summary([make_fact()], "2026-09")
        assert result["unverified_numbers"] == ["88.8"]

    @respx.mock
    async def test_default_config_has_empty_trace(self, ai_settings) -> None:
        """不传 config（升级前行为）：留痕为空、无范文护栏、不重试。"""
        route = respx.post(CHAT_URL).mock(
            return_value=chat_response("CPU 使用率 35%，环比上升 16.67%。")
        )
        result = await generate_summary([make_fact()], "2026-09")
        assert route.call_count == 1
        assert result["prompt_used"] == ""
        assert result["sample_used"] == ""
        assert result["sample_leak"] == []
        assert result["retried"] is False
        sent = json.loads(route.calls.last.request.content)
        # 无自定义输入时不追加规则 6-8，保持与升级前一致的 system 内容
        assert "本段写作要求" not in sent["messages"][0]["content"]


class TestInsightConfig:
    def test_none_yields_defaults(self) -> None:
        cfg = InsightConfig.from_mapping(None)
        assert cfg.max_chars == MAX_SUMMARY_CHARS
        assert cfg.temperature == 0.2
        assert cfg.threshold_pct is None
        assert cfg.has_custom is False

    def test_sample_longer_than_default_sets_max_chars_to_sample_length(self) -> None:
        """给了范文但没配字数 -> 以**范文长度**为准，避免仿写被 200 字截断。"""
        sample = "上期" + "甲" * 300 + "。"
        cfg = InsightConfig.from_mapping({"sample_text": sample})
        assert cfg.max_chars == len(sample)
        assert cfg.has_custom is True

    def test_short_sample_keeps_default_max_chars(self) -> None:
        """范文没超过默认值 -> 仍是 200 字（没有"自动放宽到 300"这种隐藏行为）。"""
        cfg = InsightConfig.from_mapping({"sample_text": "上期 CPU 为 88.8%。"})
        assert cfg.max_chars == MAX_SUMMARY_CHARS
        assert cfg.has_custom is True

    def test_very_long_sample_capped_at_limit(self) -> None:
        cfg = InsightConfig.from_mapping({"sample_text": "甲" * 5000})
        assert cfg.max_chars == MAX_SUMMARY_CHARS_LIMIT

    def test_explicit_max_chars_wins_over_sample_default(self) -> None:
        cfg = InsightConfig.from_mapping({"sample_text": "范文", "max_chars": 800})
        assert cfg.max_chars == 800

    def test_prompt_only_is_custom_but_no_relax(self) -> None:
        cfg = InsightConfig.from_mapping({"prompt": "只写一句话"})
        assert cfg.has_custom is True
        assert cfg.max_chars == MAX_SUMMARY_CHARS

    def test_dirty_values_fall_back(self) -> None:
        cfg = InsightConfig.from_mapping(
            {"max_chars": "abc", "temperature": "hot", "threshold_pct": -5}
        )
        assert cfg.max_chars == MAX_SUMMARY_CHARS
        assert cfg.temperature == 0.2
        assert cfg.threshold_pct == 0.1  # 负值被夹到下限

    def test_whitespace_only_is_not_custom(self) -> None:
        cfg = InsightConfig.from_mapping({"prompt": "   ", "sample_text": "\n"})
        assert cfg.has_custom is False

    def test_non_mapping_ignored(self) -> None:
        assert InsightConfig.from_mapping("oops").has_custom is False  # type: ignore[arg-type]


class TestForbiddenNumbers:
    def test_only_sample_specific_numbers_are_forbidden(self) -> None:
        """facts 中可溯源的数字不算禁抄，范文独有的才算。"""
        forbidden = forbidden_numbers("上期 CPU 为 88.8%，当时环比上升 16.67%。", [make_fact()])
        assert "88.8" in forbidden
        assert "16.67" not in forbidden

    def test_blank_sample_yields_empty(self) -> None:
        assert forbidden_numbers("   ", [make_fact()]) == set()

    def test_sample_leaks_detects_and_dedups(self) -> None:
        forbidden = {"88.8", "77.7"}
        assert sample_leaks("本期 88.8，上期也是 88.8。", forbidden) == ["88.8"]
        assert sample_leaks("本期 CPU 为 35%。", forbidden) == []


class TestSampleIsTemplate:
    """范文 = 本段输出的模板：**复用它的句式不是违规**（数字照抄仍由 TestForbiddenNumbers 覆盖）。

    线上真实诉求（2026-10-14）：范文「Gi0/0/21 上月平均 Bits received：161.11 Kbps」，
    用户期望输出就是同一句话、只把数值与单位换成本期真实值。早期版本的 sample_copy
    把「Gi0/0/21 上月平均 Bits received：262.47 Kbps」判成「整句复述范文」并触发重写
    （重试版写成「Gi0/0/21 于 2026-09 的平均入向接收速率为 262.47 Kbps。」），
    与「严格按照参考范文，不要增加超出范文的其他输出」的诉求正相反 —— 该护栏已废弃。
    """

    SAMPLE = "Gi0/0/21 上月平均 Bits received：161.11 Kbps"

    def test_reusing_sample_wording_is_legal(self) -> None:
        fact = make_fact(
            name="gi0_0_21_inbound_avg", object="Gi0/0/21", value=262.47, unit="Kbps"
        )
        forbidden = forbidden_numbers(self.SAMPLE, [fact])
        assert forbidden == {"161.11"}, "只禁范文里的示例数值"
        text = "Gi0/0/21 上月平均 Bits received：262.47 Kbps"
        assert sample_leaks(text, forbidden) == []
        assert sample_item_overrun(text, self.SAMPLE) == 0

    def test_sample_number_still_flagged(self) -> None:
        """范文里的示例值照抄，仍然要被抓出来（这是唯一该管的范文违规）。"""
        fact = make_fact(name="gi0_0_21_inbound_avg", object="Gi0/0/21", value=262.47, unit="Kbps")
        forbidden = forbidden_numbers(self.SAMPLE, [fact])
        text = "Gi0/0/21 上月平均 Bits received：161.11 Kbps"
        assert sample_leaks(text, forbidden) == ["161.11"]


class TestSampleItemOverrun:
    """范文骨架 = 输出范围与条目数上限。

    线上：范文只写「平均速率」一条，模型照范文句式又补了一条「出方向峰值」。
    数字照抄由 sample_leaks 管，但它报不出「多了一条」这件事 ——
    重试的纠正指令里也就不会提，于是重试版照样多一条。
    """

    SAMPLE = "上期入向平均速率为 161.11 Kbps。"

    def test_item_count_splits_on_newline_and_periods(self) -> None:
        assert item_count("") == 0
        assert item_count("   ") == 0
        assert item_count(self.SAMPLE) == 1
        assert item_count("甲。乙。") == 2
        assert item_count("甲\n乙\n\n丙") == 3
        assert item_count("甲；乙") == 2

    def test_extra_item_is_detected_without_any_copy(self) -> None:
        """多出一条独立数据行，既不抄范例数字也不复述 —— 只有条目数能认出来。"""
        text = "本期入向平均速率 262.47 Kbps。\n本期出方向峰值 199228840 bps。"
        assert sample_item_overrun(text, self.SAMPLE) == 1

    def test_same_or_fewer_items_is_ok(self) -> None:
        assert sample_item_overrun(self.SAMPLE, self.SAMPLE) == 0
        assert sample_item_overrun("本期入向平均速率 262.47 Kbps。", self.SAMPLE) == 0

    def test_no_sample_means_no_limit(self) -> None:
        """不提供范文时一个字都不管（行为与升级前一致）。"""
        assert sample_item_overrun("甲。乙。丙。", "") == 0
        assert sample_item_overrun("甲。乙。丙。", "   ") == 0

    def test_correction_names_the_overrun(self) -> None:
        text = build_correction([], overrun=1, sample_items=1)
        assert "范文只有 1 条" in text
        assert "条目数必须与范文骨架一致（1 条）" in text
        # 不以数字为由时，不要误报成那一类
        assert "出现了参考范文中的示例数字" not in text
        # 纠正指令必须让模型**照范文句式**重写，而不是"换一种说法"
        assert "请保持范文的句式与结构重新输出" in text
        assert "不得复用范文的句子" not in text


class TestCustomPrompt:
    @respx.mock
    async def test_prompt_and_sample_sent_with_guardrails(self, ai_settings) -> None:
        route = respx.post(CHAT_URL).mock(
            return_value=chat_response("CPU 使用率 35%，环比上升 16.67%。")
        )
        cfg = InsightConfig(
            prompt="只总结 CPU 相关指标", sample_text="上期 CPU 为 88.8%。"
        )
        result = await generate_summary([make_fact()], "2026-09", cfg)
        assert result["degraded"] is False
        assert result["prompt_used"] == "只总结 CPU 相关指标"
        assert result["sample_used"] == "上期 CPU 为 88.8%。"

        sent = json.loads(route.calls.last.request.content)
        system = sent["messages"][0]["content"]
        user = sent["messages"][1]["content"]
        # 用户输入只进 user message；system 固化红线并声明不可被覆盖
        assert "本段写作要求" in system
        assert "不得改变第 1-5 条规则" in system
        # 规则 8 的语义是「范文 = 输出模板」：只换数值与单位，示例数字禁抄
        assert "范文里的数字是**示例值**" in system
        assert "范文的条目数就是输出的条目数" in system
        assert "不得复用范文的句子" not in system
        assert "只总结 CPU 相关指标" in user
        assert "上期 CPU 为 88.8%" in user
        # 范文与指标数据同处 user，标注为「输出的模板」而非"风格样例"
        assert "参考范文" in user
        assert "本段输出的模板" in user

    @respx.mock
    async def test_sample_leak_triggers_retry_and_uses_fix(self, ai_settings) -> None:
        route = respx.post(CHAT_URL).mock(
            side_effect=[
                chat_response("本期 CPU 为 88.8%。"),
                chat_response("CPU 使用率 35%，环比上升 16.67%。"),
            ]
        )
        cfg = InsightConfig(sample_text="上期 CPU 为 88.8%。")
        result = await generate_summary([make_fact()], "2026-09", cfg)
        assert route.call_count == 2
        assert result["retried"] is True
        assert result["sample_leak"] == []
        assert "88.8" not in result["text"]

    @respx.mock
    async def test_reusing_sample_sentence_does_not_retry(self, ai_settings) -> None:
        """照范文句式写、只把数值换成本期值 —— 期望行为，绝不能触发重写。

        线上真实诉求（2026-10-14）：范文「上期入向平均速率为 161.11 Kbps。」，
        期望输出「本期入向平均速率为 262.47 Kbps。」。早期版本按"整句复述范文"
        把这段正确答案重写成另一个句子，反而偏离了用户要求（该护栏已废弃）。
        """
        sample = "上期入向平均速率为 161.11 Kbps。"
        text = "本期入向平均速率为 262.47 Kbps。"
        route = respx.post(CHAT_URL).mock(return_value=chat_response(text))
        facts = [make_fact(name="inbound_avg", value=262.47, unit="Kbps")]
        result = await generate_summary(facts, "2026-09", InsightConfig(sample_text=sample))
        assert route.call_count == 1, "句式照用不该多打一次 AI"
        assert result["retried"] is False
        assert result["text"] == text
        assert result["sample_leak"] == []

    @respx.mock
    async def test_extra_item_beyond_sample_skeleton_triggers_retry(self, ai_settings) -> None:
        """线上真实故障：范文只写了「平均速率」一条，模型照范文句式又补了一条「出方向峰值」。"""
        sample = "上期入向平均速率为 161.11 Kbps。"
        extra = "本期入向平均速率 262.47 Kbps。\n本期出方向峰值 199228840 bps。"
        fix = "本期入向平均速率 262.47 Kbps。"
        route = respx.post(CHAT_URL).mock(
            side_effect=[chat_response(extra), chat_response(fix)]
        )
        facts = [
            make_fact(name="inbound_avg", value=262.47, unit="Kbps"),
            make_fact(name="outbound_peak", value=199228840.0, unit="bps"),
        ]
        result = await generate_summary(facts, "2026-09", InsightConfig(sample_text=sample))
        assert route.call_count == 2
        assert result["retried"] is True
        assert result["text"] == fix
        assert "199228840" not in result["text"]
        correction = json.loads(route.calls.last.request.content)["messages"][-1]["content"]
        assert "范文只有 1 条" in correction

    @respx.mock
    async def test_matching_item_count_does_not_retry(self, ai_settings) -> None:
        """范文 1 条、输出 1 条 —— 不该为条目数白白多打一次 AI（防误伤）。"""
        route = respx.post(CHAT_URL).mock(
            return_value=chat_response("本期入向平均速率 262.47 Kbps。")
        )
        result = await generate_summary(
            [make_fact(name="inbound_avg", value=262.47, unit="Kbps")],
            "2026-09",
            InsightConfig(sample_text="上期入向平均速率为 161.11 Kbps。"),
        )
        assert route.call_count == 1
        assert result["retried"] is False

    @respx.mock
    async def test_retry_keeps_version_with_fewer_leaks(self, ai_settings) -> None:
        respx.post(CHAT_URL).mock(
            side_effect=[
                chat_response("本期 88.8 与 77.7 都异常。"),
                chat_response("本期 88.8 偏高。"),
            ]
        )
        cfg = InsightConfig(sample_text="上期 88.8 与 77.7。")
        result = await generate_summary([make_fact()], "2026-09", cfg)
        assert result["retried"] is True
        assert result["sample_leak"] == ["88.8"]

    @respx.mock
    async def test_prompt_only_does_not_retry(self, ai_settings) -> None:
        route = respx.post(CHAT_URL).mock(
            return_value=chat_response("CPU 35%，环比上升 16.67%。")
        )
        result = await generate_summary(
            [make_fact()], "2026-09", InsightConfig(prompt="简洁一点")
        )
        assert route.call_count == 1
        assert result["retried"] is False
        assert result["sample_leak"] == []

    @respx.mock
    async def test_custom_max_chars_respected(self, ai_settings) -> None:
        long_text = "甲" * 100 + "。" + "乙" * 400 + "。"
        respx.post(CHAT_URL).mock(return_value=chat_response(long_text))
        result = await generate_summary(
            [make_fact()], "2026-09", InsightConfig(max_chars=600)
        )
        assert len(result["text"]) <= 600
        assert result["text"].endswith("。")

    async def test_degraded_keeps_prompt_trace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AI 不可用时也要留痕：便于审计"这段当初配了什么"。"""
        monkeypatch.setattr(get_settings(), "ai_api_key", "")
        result = await generate_summary(
            [make_fact()], "2026-09", InsightConfig(prompt="只写一句话")
        )
        assert result["degraded"] is True
        assert result["prompt_used"] == "只写一句话"

    @respx.mock
    async def test_threshold_override_injected(self, ai_settings) -> None:
        route = respx.post(CHAT_URL).mock(
            return_value=chat_response("CPU 使用率 35%。")
        )
        await generate_summary(
            [make_fact()], "2026-09", InsightConfig(threshold_pct=25)
        )
        system = json.loads(route.calls.last.request.content)["messages"][0]["content"]
        assert "25%" in system


class TestRenderSystemPrompt:
    """system 文本开放给管理员编辑后，渲染必须**占位符安全**。

    升级前用 `SYSTEM_PROMPT.format(...)`，因此默认文本里 `{"series": [...]}`
    不得不写成 `{{...}}`；一旦开放编辑，用户文本里任何一个裸 `{` 都会抛
    KeyError / ValueError，整篇摘要直接崩。
    """

    def test_placeholders_are_replaced(self) -> None:
        out = render_system_prompt(
            "波动 > {threshold_pct}%，不超过 {max_chars} 字",
            threshold_pct=12.5,
            max_chars=300,
        )
        assert out == "波动 > 12.5%，不超过 300 字"

    def test_threshold_uses_g_format_like_before(self) -> None:
        """沿用 f"{x:g}" 语义（10.0 -> "10"），与升级前逐字一致。"""
        assert render_system_prompt("{threshold_pct}", threshold_pct=10.0, max_chars=200) == "10"

    def test_bare_braces_are_preserved(self) -> None:
        """裸花括号原样保留 —— 规则 1 里的 JSON 片段就是这种情况。"""
        text = 'value 形如 {"series": [...]}，共 {} 项'
        assert render_system_prompt(text, threshold_pct=10, max_chars=200) == text

    def test_unknown_placeholder_is_not_touched(self) -> None:
        text = "价格 {currency}，字数 {max_chars}"
        assert render_system_prompt(text, threshold_pct=10, max_chars=200) == (
            "价格 {currency}，字数 200"
        )

    def test_empty_text(self) -> None:
        assert render_system_prompt("", threshold_pct=10, max_chars=200) == ""

    def test_default_prompt_no_longer_needs_escaping(self) -> None:
        """默认常量里的字面花括号必须是单花括号（不再需要 format 转义）。"""
        assert '{"series": [...]}' in SYSTEM_PROMPT
        assert "{{" not in SYSTEM_PROMPT


class TestResolveSystemPrompt:
    """两段各自独立的三级回退：绑定级 -> 全局 -> 内置常量。

    **不能合并成一段**：追加规则只在绑定了写作要求/范文时才生效
    （has_custom），合并会让规则 6-8 泄漏到所有摘要。
    """

    def test_fallback_to_code_defaults(self) -> None:
        base, extra = resolve_system_prompt(InsightConfig(), get_settings())
        assert base == SYSTEM_PROMPT
        assert extra == SYSTEM_PROMPT_USER_INPUT

    def test_global_overrides_code_default(self) -> None:
        settings = get_settings().model_copy(
            update={"ai_system_prompt": "全局基础", "ai_system_rules": "全局追加"}
        )
        base, extra = resolve_system_prompt(InsightConfig(), settings)
        assert (base, extra) == ("全局基础", "全局追加")

    def test_binding_overrides_global(self) -> None:
        settings = get_settings().model_copy(
            update={"ai_system_prompt": "全局基础", "ai_system_rules": "全局追加"}
        )
        config = InsightConfig(system_prompt="本段基础")
        base, extra = resolve_system_prompt(config, settings)
        assert base == "本段基础"
        assert extra == "全局追加", "只覆盖一段时另一段仍走全局"

    def test_blank_text_falls_back(self) -> None:
        """库里存了空白串时不能当自定义（否则规则会变成空白）。"""
        settings = get_settings().model_copy(
            update={"ai_system_prompt": "   ", "ai_system_rules": "\n"}
        )
        base, extra = resolve_system_prompt(InsightConfig(), settings)
        assert base == SYSTEM_PROMPT
        assert extra == SYSTEM_PROMPT_USER_INPUT

    def test_from_mapping_reads_and_clips(self) -> None:
        cfg = InsightConfig.from_mapping(
            {"system_prompt": "  本段基础  ", "system_rules": "x" * 5000}
        )
        assert cfg.system_prompt == "本段基础"
        assert len(cfg.system_rules) == 4000

    def test_from_mapping_ignores_dirty_values(self) -> None:
        cfg = InsightConfig.from_mapping({"system_prompt": None, "system_rules": 123})
        assert cfg.system_prompt == ""
        assert cfg.system_rules == "123"


class TestBuildMessagesRendering:
    """无自定义文本时，渲染结果与升级前的 format() 逐字相同（回归锁）。"""

    def test_matches_legacy_format_output(self) -> None:
        """默认文本去掉转义后与升级前**逐字相同**。

        升级前的常量里`{"series"` 写成 `{{"series"`（为了能走 format），
        这里先还原成升级前的样子再 format，两者必须完全一致。
        """
        config = InsightConfig(max_chars=200)
        settings = get_settings()
        messages = build_messages([make_fact()], "2026-09", config, 12.5, settings=settings)
        legacy_src = SYSTEM_PROMPT.replace('{"series": [...]}', '{{"series": [...]}}')
        legacy = legacy_src.format(threshold_pct=f"{12.5:g}", max_chars=200)
        assert messages[0]["content"] == legacy
        assert '{"series": [...]}' in messages[0]["content"]

    def test_extra_rules_only_when_has_custom(self) -> None:
        settings = get_settings()
        plain = build_messages([make_fact()], "2026-09", InsightConfig(), 10, settings=settings)
        assert SYSTEM_PROMPT_USER_INPUT.strip() not in plain[0]["content"]
        with_prompt = build_messages(
            [make_fact()], "2026-09", InsightConfig(prompt="只说 CPU"), 10, settings=settings
        )
        assert SYSTEM_PROMPT_USER_INPUT.strip() in with_prompt[0]["content"]

    def test_custom_text_renders_placeholders(self) -> None:
        settings = get_settings()
        config = InsightConfig(system_prompt="上限 {max_chars} 字")
        messages = build_messages([make_fact()], "2026-09", config, 10, settings=settings)
        assert messages[0]["content"] == "上限 200 字"

    @respx.mock
    async def test_generate_summary_uses_resolved_text(
        self, ai_settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """端到端：全局自定义文本真的进了请求（省掉重启即生效的关键）。

        必须用 monkeypatch 改`get_settings()`：`get_settings` 是 `@lru_cache`
        单例，直接赋值会**污染整个 pytest 进程**（全量跑时后面的用例会看到这份
        "全局规则"，表现为 order-dependent 的诡异失败）。
        """
        route = respx.post(CHAT_URL).mock(
            return_value=chat_response("CPU 使用率 35%，环比上升 16.67%。")
        )
        monkeypatch.setattr(ai_settings, "ai_system_prompt", "只准说 CPU，其它一律不提。")
        monkeypatch.setattr(ai_settings, "ai_system_rules", "")
        await generate_summary([make_fact()], "2026-09", InsightConfig())
        sent = json.loads(route.calls.last.request.content)
        assert sent["messages"][0]["content"] == "只准说 CPU，其它一律不提。"