"""总结类占位的注入与安全网（2026-11-03）。

背景：总结类占位（``{{ summary_xxx }}``）**不参与模板绑定**，由确认页上传
文件后 AI 生成、确认时快照、渲染时注入。注入逻辑有三处容易出问题：

1. **只注入模板里确实存在的占位** —— 多注入的会让 context 出现模板没有的 key，
   ``check_context`` 判为「多余」报错，整份报告渲染失败
2. **模板里的总结占位全部要有值** —— 没内容就填提示文字；docxtpl 的 Jinja
   对未定义变量会直接抛 UndefinedError
3. **占位名必须是 summary_ 前缀** —— 中文名（如「总结_xxx」）不符合
   ``docx_parser.VAR_RE``，会被判为非法语法
"""

import asyncio

import pytest

from app.services.docx_renderer import (
    SUMMARY_PLACEHOLDER_TEXT,
    check_context,
    is_summary_placeholder,
)


class TestSummaryInjection:
    def test_only_template_placeholders_injected(self) -> None:
        """模板里没有的占位不能进 context（否则 check_context 判「多余」）。"""
        from app.services.preview_service import build_context

        context = build_context(
            [],
            {},
            {},
            {},
            summary_texts={"summary_workorder": "总结内容", "summary_other": "多余"},
            template_summary_placeholders=["summary_workorder"],
        )
        assert "summary_workorder" in context
        assert "summary_other" not in context
        # 精确匹配：模板声明与 context 完全一致
        check_context({"summary_workorder"}, context)

    def test_template_placeholder_without_text_gets_placeholder_text(self) -> None:
        """模板里的总结占位即使没有内容也要有值（否则 Jinja UndefinedError）。"""
        from app.services.preview_service import build_context

        context = build_context(
            [],
            {},
            {},
            {},
            summary_texts={},
            template_summary_placeholders=["summary_workorder"],
        )
        assert context["summary_workorder"] == SUMMARY_PLACEHOLDER_TEXT
        check_context({"summary_workorder"}, context)

    def test_missing_placeholder_not_required_by_check_context(self) -> None:
        """「总结_」占位缺失不算错误（运行时填充，不参与绑定）。"""
        check_context({"summary_workorder"}, {})

    def test_non_summary_placeholder_still_required(self) -> None:
        """普通指标占位缺失仍然要报错。"""
        with pytest.raises(Exception):
            check_context({"cpu_usage"}, {})


class TestPlaceholderNaming:
    def test_summary_prefix(self) -> None:
        assert is_summary_placeholder("summary_workorder")
        assert not is_summary_placeholder("总结_workorder")
        assert not is_summary_placeholder("cpu_usage")

    def test_save_summary_rejects_non_summary_placeholder(self) -> None:
        """落库时校验占位名 —— 中文名会在渲染阶段炸掉，不能放进去。"""
        from app.services.report_summary_service import save_summary

        class _FakeDB:
            async def scalar(self, *a, **k):
                return None

        with pytest.raises(ValueError, match="总结"):
            asyncio.run(
                save_summary(
                    _FakeDB(),  # type: ignore[arg-type]
                    None,  # type: ignore[arg-type]
                    None,  # type: ignore[arg-type]
                    placeholder="总结_workorder",
                    prompt="",
                    text="x",
                )
            )
