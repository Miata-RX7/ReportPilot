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
