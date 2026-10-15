"""docx_renderer 单元测试。"""

import hashlib
from pathlib import Path

import pytest

from app.services.docx_renderer import RenderError, render_docx
from tests.docx_factory import make_docx, read_docx_text


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestRender:
    def test_full_context_replaced(self, tmp_path: Path) -> None:
        tpl = make_docx(
            tmp_path / "tpl.docx",
            paragraphs=["CPU: {{ cpu }}", "摘要: {{ summary }}"],
        )
        out = render_docx(
            tpl, {"cpu": "35", "summary": "[AI摘要待生成]"}, tmp_path / "out.docx"
        )
        text = read_docx_text(out)
        assert "CPU: 35" in text
        assert "摘要: [AI摘要待生成]" in text
        assert "{{" not in text

    def test_missing_context_var(self, tmp_path: Path) -> None:
        tpl = make_docx(tmp_path / "tpl.docx", paragraphs=["{{ a }} {{ b }}"])
        with pytest.raises(RenderError, match="缺失"):
            render_docx(tpl, {"a": "1"}, tmp_path / "out.docx")

    def test_extra_context_var(self, tmp_path: Path) -> None:
        tpl = make_docx(tmp_path / "tpl.docx", paragraphs=["{{ a }}"])
        with pytest.raises(RenderError, match="多余"):
            render_docx(tpl, {"a": "1", "b": "2"}, tmp_path / "out.docx")

    def test_original_template_untouched(self, tmp_path: Path) -> None:
        tpl = make_docx(tmp_path / "tpl.docx", paragraphs=["{{ a }}"])
        before = _hash(tpl)
        render_docx(tpl, {"a": "x"}, tmp_path / "out.docx")
        assert _hash(tpl) == before

    def test_sandbox_blocks_attribute_access(self, tmp_path: Path) -> None:
        # 即便绕过上传扫描，SandboxedEnvironment 也会把下划线属性访问中和为空
        tpl = make_docx(
            tmp_path / "tpl.docx", paragraphs=["{{ a.__class__ }}"]
        )
        out = render_docx(tpl, {"a": "x"}, tmp_path / "out.docx")
        assert "<class" not in read_docx_text(out)

    def test_split_run_variable_replaced(self, tmp_path: Path) -> None:
        tpl = make_docx(
            tmp_path / "tpl.docx", split_runs=[["{{met", "ric}}"]]
        )
        out = render_docx(tpl, {"metric": "99.92"}, tmp_path / "out.docx")
        assert "99.92" in read_docx_text(out)
