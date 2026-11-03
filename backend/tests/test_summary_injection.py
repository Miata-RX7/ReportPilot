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
import pytest_asyncio

from app.services.docx_renderer import (
    SUMMARY_PLACEHOLDER_TEXT,
    check_context,
    is_summary_placeholder,
)


@pytest_asyncio.fixture
async def db():
    """内存库：给需要落库的用例用。"""
    import pytest_asyncio
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


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


class TestSummaryNotBindable:
    """总结类占位**不参与绑定**（用户 2026-11-03 反馈：绑定时提示「未完成绑定」）。

    前端把它标成 summary 类型、不参与未绑定校验、不提交给后端；
    后端也明确拒绝并给出可操作的提示，而不是含糊的「绑定的指标不存在」。
    """

    def test_frontend_prefix_helper(self) -> None:
        """前端用的判定与后端保持一致：summary_ 前缀。"""
        from app.services.docx_renderer import SUMMARY_PLACEHOLDER_PREFIX

        assert SUMMARY_PLACEHOLDER_PREFIX == "summary_"

    def test_backend_rejects_summary_binding(self) -> None:
        """提交 summary_ 占位的绑定 -> schema 层就拒绝，且提示要说清原因。

        第一道防线在 schema（BindingItem 的校验器）：总结类占位不参与绑定，
        报错必须说「不需要绑定」，而不是让人以为是「指标绑定缺参数」。
        """
        from pydantic import ValidationError

        from app.schemas.template import BindingsUpdate

        with pytest.raises(ValidationError) as exc:
            BindingsUpdate(
                bindings=[
                    {
                        "placeholder": "summary_workorder",
                        "binding_type": "metric",
                        "binding_ref_id": None,
                    }
                ]
            )
        msg = str(exc.value)
        assert "summary_workorder" in msg
        assert "不需要绑定" in msg


class TestUnboundExcludesSummary:
    """「未绑定」判定必须排除总结类占位。

    线上事故（2026-11-03）：报告任务手动触发生成失败，提示
    「以下变量未绑定: ['summary_workorder']」—— 生成路径上的未绑定校验
    （preview_service.compute_template_facts）没排除总结类占位。
    """

    def test_bindable_placeholders_excludes_summary(self) -> None:
        from app.api.templates import _bindable_placeholders
        from app.models.template import TemplateVersion

        v = TemplateVersion(
            template_id=1,
            version_no=1,
            file_path="/tmp/a.docx",
            placeholders_json=["summary_workorder", "cpu_usage", "aaa"],
        )
        assert _bindable_placeholders(v) == ["cpu_usage", "aaa"]

    def test_bindable_placeholders_none_version(self) -> None:
        from app.api.templates import _bindable_placeholders

        assert _bindable_placeholders(None) == []

    @pytest.mark.anyio
    async def test_compute_template_facts_allows_unbound_summary(
        self, db, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """模板里有未绑定的 summary_ 占位时，计算阶段不应报错。"""
        from app.models.template import TemplateVersion, WordTemplate
        from app.services.preview_service import compute_template_facts

        tpl = WordTemplate(name="t", file_path="/tmp/t.docx", owner_id=1)
        db.add(tpl)
        await db.flush()
        v = TemplateVersion(
            template_id=tpl.id,
            version_no=1,
            file_path="/tmp/t.docx",
            placeholders_json=["summary_workorder"],
        )
        db.add(v)
        await db.commit()

        bindings, facts, metrics = await compute_template_facts(db, v, "2026-09")
        assert bindings == []
        assert facts == {}
