"""docxtpl 渲染：SandboxedEnvironment（上传扫描之外的第二道防线）。

图表绑定处 context 的值是 docxtpl 的 InlineImage（不是 str），因此
context 类型为 dict[str, Any]。实测 InlineImage 与 SandboxedEnvironment 兼容，
无需为图片放宽沙箱。
"""

import logging
from pathlib import Path
from typing import Any

from docxtpl import DocxTemplate
from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger(__name__)


class RenderError(Exception):
    """渲染失败（变量不一致 / 模板损坏等）。消息不含敏感信息。"""


def open_template(template_path: Path) -> DocxTemplate:
    """打开模板（不做变量校验）。

    **调用方要嵌入图片时必须复用本函数返回的实例**构造 InlineImage
    （InlineImage 需要 DocxTemplate 实例），并把同一个实例通过 render_docx
    的 tpl 参数传回来，避免重复解析。

    注意：构造 InlineImage 前后不要调用 tpl.get_xml()，那会让
    _inline_shapes 置空，后续插图报
    'NoneType' object has no attribute '_element'。
    """
    try:
        return DocxTemplate(str(template_path))
    except Exception as exc:
        raise RenderError(f"模板解析失败: {type(exc).__name__}") from exc


def template_variables(tpl: DocxTemplate) -> set[str]:
    """模板中声明的变量集合（以 docxtpl 为准，与上传时解析可能不一致）。"""
    try:
        return set(tpl.get_undeclared_template_variables())
    except Exception as exc:
        raise RenderError(f"模板解析失败: {type(exc).__name__}") from exc


def check_context(tpl_vars: set[str], context: dict[str, Any]) -> None:
    """context 与模板变量必须精确一致（缺失/多余均报错）。"""
    ctx_vars = set(context.keys())
    if tpl_vars != ctx_vars:
        missing = sorted(tpl_vars - ctx_vars)
        extra = sorted(ctx_vars - tpl_vars)
        raise RenderError(
            f"context 与模板变量不一致: 缺失 {missing}, 多余 {extra}"
        )


def render_docx(
    template_path: Path,
    context: dict[str, Any],
    output_path: Path,
    *,
    tpl: DocxTemplate | None = None,
) -> Path:
    """渲染模板到 output_path。只读原模板，不修改原文件。

    tpl：调用方已通过 open_template 创建实例（用于构造 InlineImage）时传入，
    保证图片与渲染使用同一实例。
    """
    tpl = tpl if tpl is not None else open_template(template_path)
    check_context(template_variables(tpl), context)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        tpl.render(context, jinja_env=SandboxedEnvironment())
        tpl.save(str(output_path))
    except Exception as exc:
        raise RenderError(f"模板渲染失败: {type(exc).__name__}") from exc
    logger.info("渲染完成 %s -> %s", template_path.name, output_path.name)
    return output_path
