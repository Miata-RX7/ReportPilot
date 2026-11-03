"""docx_parser 单元测试：变量解析与语法安全扫描。"""

from pathlib import Path

import pytest

from app.services.docx_parser import TemplateSyntaxError, parse_template
from tests.docx_factory import make_docx


class TestParse:
    def test_standard_variables(self, tmp_path: Path) -> None:
        path = make_docx(
            tmp_path / "t.docx",
            paragraphs=["CPU: {{ cpu_usage }}", "内存 {{ mem_usage }} 与 {{cpu_usage}}"],
        )
        # 去重 + 保持文档出现顺序（非字母序）
        assert parse_template(path).names == ["cpu_usage", "mem_usage"]

    def test_order_preserved(self, tmp_path: Path) -> None:
        path = make_docx(
            tmp_path / "t.docx", paragraphs=["{{ b_var }}", "{{ a_var }}"]
        )
        assert parse_template(path).names == ["b_var", "a_var"]

    def test_table_cell_variable(self, tmp_path: Path) -> None:
        path = make_docx(
            tmp_path / "t.docx", table_cells=["可用率: {{ avail_rate }}"]
        )
        assert parse_template(path).names == ["avail_rate"]

    def test_header_variable(self, tmp_path: Path) -> None:
        path = make_docx(
            tmp_path / "t.docx",
            paragraphs=["正文"],
            header_texts=["报告期: {{ period_label }}"],
        )
        assert parse_template(path).names == ["period_label"]

    def test_split_run_variable(self, tmp_path: Path) -> None:
        # Word 常把 {{var}} 拆进多个 run，拼接段落文本后应完整识别
        path = make_docx(
            tmp_path / "t.docx", split_runs=[["{{met", "ric_cpu", "}}"]]
        )
        assert parse_template(path).names == ["metric_cpu"]

    def test_no_variables(self, tmp_path: Path) -> None:
        path = make_docx(tmp_path / "t.docx", paragraphs=["纯文本，没有变量"])
        assert parse_template(path).names == []


class TestReject:
    @pytest.mark.parametrize(
        "bad",
        [
            "{{ a.b }}",
            "{{ a|upper }}",
            "{% if x %}",
            "{% endif %}",
            "{{ a+1 }}",
            "{{ a[0] }}",
            "{{ 1abc }}",
            "{{ 中文变量 }}",
            "{{ a-b }}",
        ],
    )
    def test_complex_syntax_rejected(self, tmp_path: Path, bad: str) -> None:
        path = make_docx(tmp_path / "t.docx", paragraphs=[f"内容 {bad} 内容"])
        with pytest.raises(TemplateSyntaxError) as exc_info:
            parse_template(path)
        assert any(bad in v for v in exc_info.value.violations)

    def test_unclosed_delimiter_rejected(self, tmp_path: Path) -> None:
        path = make_docx(tmp_path / "t.docx", paragraphs=["未闭合 {{ abc"])
        with pytest.raises(TemplateSyntaxError) as exc_info:
            parse_template(path)
        assert any("{{" in v for v in exc_info.value.violations)

    def test_stray_close_rejected(self, tmp_path: Path) -> None:
        path = make_docx(tmp_path / "t.docx", paragraphs=["孤立闭括号 abc }}"])
        with pytest.raises(TemplateSyntaxError):
            parse_template(path)

    def test_violation_in_table_rejected(self, tmp_path: Path) -> None:
        path = make_docx(tmp_path / "t.docx", table_cells=["{{ a.b }}"])
        with pytest.raises(TemplateSyntaxError):
            parse_template(path)


class TestPlaceholderNamingRules:
    """变量名的合法字符集 —— 回归（2026-11-03 线上事故）。

    背景：平台约定的「总结类」占位曾设计成 ``{{ 总结_xxx }}``（中文前缀），
    但 VAR_RE 只允许 ASCII 标识符（Jinja/docxtpl 的变量名也必须如此），
    于是用户照着提示在 Word 里输入中文占位后，ONLYOFFICE 保存被模板语法
    校验拒绝 -> 不生成新版本 -> 界面却显示「已保存」，编辑内容丢失。

    现在总结类占位统一用 ASCII 前缀 ``summary_``，并保留这条测试锁住规则。
    """

    def test_ascii_name_accepted(self) -> None:
        from app.services.docx_parser import VAR_RE

        m = VAR_RE.match("{{ summary_workorder }}")
        assert m is not None and m.group(1) == "summary_workorder"

    def test_chinese_name_rejected(self) -> None:
        from app.services.docx_parser import VAR_RE

        assert VAR_RE.match("{{ 总结_工单汇总 }}") is None

    def test_spaces_around_name_allowed(self) -> None:
        from app.services.docx_parser import VAR_RE

        m = VAR_RE.match("{{ summary_workorder }}")
        assert m is not None and m.group(1) == "summary_workorder"

    def test_summary_prefix_detected(self) -> None:
        from app.services.docx_renderer import (
            SUMMARY_PLACEHOLDER_PREFIX,
            is_summary_placeholder,
        )

        assert SUMMARY_PLACEHOLDER_PREFIX == "summary_"
        assert is_summary_placeholder("summary_workorder")
        assert not is_summary_placeholder("cpu_usage")
