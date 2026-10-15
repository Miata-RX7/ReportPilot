"""模板渲染编排（preview 与 report 共用的底层逻辑）。

facts/insights 分离：metric 绑定的值来自 calc_engine（facts）；
insight 槽位文本由调用方注入（preview 用占位文本，report 用 AI 摘要）。
"""

import io
import logging
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from docx.shared import Mm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import load_adapter_and_name
from app.config import get_settings
from app.models.metric import MetricDefinition
from app.models.template import TemplateBinding, TemplateVersion
from app.schemas.metric import MetricFact
from app.services.adapters.base import DataSourceAdapter
from app.services.calc_engine import MetricComputeError, compute_metric, pie_labels
from app.services.chart_renderer import render_fact_chart
from app.services.docx_renderer import RenderError, open_template, render_docx

logger = logging.getLogger(__name__)

# 图表类型 -> Word 内插入宽度（mm）。与 chart_renderer 的画布物理尺寸一致，
# 保证 1:1 无缩放模糊。
CHART_WIDTH_MM = {"series": 140, "pie": 100}

# insight 槽位 key（预留多槽位扩展，避免魔法字符串散落）
INSIGHT_SLOT_SUMMARY = "summary"
# Phase 2 接入 AI 摘要后 preview 仍可复用此占位（方括号在 Word 中更显眼）
AI_SUMMARY_PLACEHOLDER = "[AI摘要待生成]"


class PreviewError(Exception):
    """渲染编排失败（未绑定 / 计算失败 / 模板不一致）。消息不含敏感信息。"""


def format_fact_value(value: float, display_precision: int | None) -> str:
    """指标值渲染格式化：None -> %g；N -> 固定 N 位小数。"""
    if display_precision is None:
        return f"{value:g}"
    return f"{value:.{display_precision}f}"


# 紧贴数值的单位符号（不加空格）："95.20%" 而不是 "95.20 %"。
# 与前端 utils/unit.ts 的 joinValueUnit 保持一致（两侧各有单测锁定）。
TIGHT_UNITS = ("%", "‰", "°", "°C", "℃")


def join_value_unit(value_text: str, unit: str) -> str:
    """数值 + 单位拼接：符号类单位紧贴数值，其余单位空格分隔。"""
    unit = (unit or "").strip()
    if not unit:
        return value_text
    if unit in TIGHT_UNITS:
        return f"{value_text}{unit}"
    return f"{value_text} {unit}"


def format_scalar_display(fact: MetricFact, metric: MetricDefinition) -> str:
    """scalar 占位符的展示文本：换算后的值 + 展示单位（未换算时回退事实值与原单位）。

    换算后 Word 占位符直接出「262.47 Kbps」而不是只出数值 —— **模板里原本手写的
    单位文字需要删除，否则会重复**（升级说明里已提示）。
    """
    value = fact.display_value if fact.display_value is not None else fact.value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)  # 防御：非数值形态不该走到这里
    precision = (
        fact.display_precision if fact.display_precision is not None else metric.display_precision
    )
    unit = fact.display_unit if fact.display_unit is not None else fact.unit
    return join_value_unit(format_fact_value(float(value), precision), unit)


async def compute_template_facts(
    db: AsyncSession,
    version: TemplateVersion,
    period: str,
) -> tuple[
    list[TemplateBinding],
    dict[str, MetricFact],
    dict[str, MetricDefinition],
]:
    """校验绑定并计算某版本模板的全部 metric，返回 (bindings, placeholder->fact, placeholder->metric)。

    - 未绑定的模板变量 -> PreviewError 并列出全部变量名（不静默空串）
    - 同一数据源只构建一次 Adapter；同一 metric 只计算一次
    - 任一 metric 计算失败 -> PreviewError 指明变量名
    """
    bindings = list(
        (
            await db.scalars(
                select(TemplateBinding).where(
                    TemplateBinding.version_id == version.id
                )
            )
        ).all()
    )
    bound = {b.placeholder for b in bindings}
    unbound = [p for p in version.placeholders_json if p not in bound]
    if unbound:
        raise PreviewError(f"以下变量未绑定: {unbound}")

    metric_bindings = [b for b in bindings if b.binding_type == "metric"]
    metric_ids = {
        b.binding_ref_id for b in metric_bindings if b.binding_ref_id is not None
    }
    metrics_by_id: dict[int, MetricDefinition] = {}
    if metric_ids:
        rows = (
            await db.scalars(
                select(MetricDefinition).where(MetricDefinition.id.in_(metric_ids))
            )
        ).all()
        metrics_by_id = {m.id: m for m in rows}

    adapters: dict[int, tuple[DataSourceAdapter, str]] = {}
    facts_by_metric: dict[int, MetricFact] = {}
    facts_by_ph: dict[str, MetricFact] = {}
    metrics_by_ph: dict[str, MetricDefinition] = {}
    for b in metric_bindings:
        metric = metrics_by_id.get(b.binding_ref_id)  # type: ignore[arg-type]
        if metric is None or not metric.is_active:
            raise PreviewError(f"变量 {b.placeholder} 绑定的指标不存在或已停用")
        is_time = (metric.metric_type or "scalar") == "time"
        if not is_time and metric.data_source_id not in adapters:
            adapters[metric.data_source_id] = await load_adapter_and_name(
                db, metric.data_source_id
            )
        if metric.id not in facts_by_metric:
            # time 类型没有数据源，adapter/source_name 传空
            adapter = source_name = None
            if not is_time:
                adapter, source_name = adapters[metric.data_source_id]
            try:
                fact = await compute_metric(metric, period, adapter, source_name)
                # 把指标的小数精度写入 fact，供报告详情/前端按 display_precision 格式化。
                # 开启单位换算时 calc_engine 已补好展示位数（含默认 2 位），不能覆盖。
                if fact.display_precision is None:
                    fact.display_precision = metric.display_precision
                facts_by_metric[metric.id] = fact
            except MetricComputeError as exc:
                raise PreviewError(
                    f"变量 {b.placeholder} 计算失败: {exc}"
                ) from None
            logger.info(
                "模板指标计算完成 template_id=%d version=%d metric=%s placeholder=%s",
                version.template_id,
                version.version_no,
                metric.name,
                b.placeholder,
            )
        facts_by_ph[b.placeholder] = facts_by_metric[metric.id]
        metrics_by_ph[b.placeholder] = metric
    return bindings, facts_by_ph, metrics_by_ph


def collect_facts(facts_by_ph: dict[str, MetricFact]) -> list[MetricFact]:
    """按 metric_id 去重后的全量 facts（AI 摘要的唯一数值输入）。

    多个占位符可绑定同一指标，需去重后再送 AI，避免同一指标重复出现。
    顺序按 facts_by_ph 的插入序（即绑定的解析顺序），保持稳定可复现。
    """
    facts: list[MetricFact] = []
    seen: set[int] = set()
    for fact in facts_by_ph.values():
        if fact.metric_id in seen:
            continue
        seen.add(fact.metric_id)
        facts.append(fact)
    return facts


def make_image_factory(tpl: Any) -> Callable[..., Any]:
    """构造 InlineImage 工厂：渲染 PNG -> 包装为 docxtpl InlineImage。

    PNG 不落盘（BytesIO 直接喂给 InlineImage），因此没有临时文件清理与
    孤儿文件问题；报告重生成时重跑 compute -> 重新画即可，无需缓存失效。

    注意：tpl 必须是 docx_renderer.open_template 返回的实例，且同一实例要
    传给 render_docx(tpl=...)。不要在此之前调用 tpl.get_xml()。
    """
    from docxtpl import InlineImage

    def factory(fact: MetricFact, metric: MetricDefinition | None = None) -> Any:
        started = time.perf_counter()
        labels = (
            pie_labels(metric) if (metric is not None and fact.value_shape == "pie") else None
        )
        png = render_fact_chart(fact, pie_labels=labels)
        width = Mm(CHART_WIDTH_MM.get(fact.value_shape, 140))
        logger.info(
            "chart rendered: shape=%s metric=%s labels=%s bytes=%d elapsed=%dms",
            fact.value_shape,
            fact.name,
            labels or "-",
            len(png),
            int((time.perf_counter() - started) * 1000),
        )
        return InlineImage(tpl, io.BytesIO(png), width=width)

    return factory


def build_context(
    bindings: list[TemplateBinding],
    facts_by_ph: dict[str, MetricFact],
    metrics_by_ph: dict[str, MetricDefinition],
    insight_texts: dict[str, str],
    *,
    image_factory: Callable[[MetricFact], Any] | None = None,
) -> dict[str, Any]:
    """构建渲染 context。insight 槽位文本由调用方注入（槽位 key -> 文本）。

    按 fact.value_shape 分支（MetricFact.value 为 float | dict）：
    - scalar：按展示口径格式化（换算后的值 + 展示单位，见 format_scalar_display）
    - text：取 payload 里的文本
    - series / pie：渲染为 PNG 并包装成 InlineImage（阶段 3A 起支持 curve；
      pie 在 3B 接上）。未提供 image_factory 时显式报错，绝不把 dict 塞进 Word。
    """
    context: dict[str, Any] = {}
    for b in bindings:
        if b.binding_type == "metric":
            fact = facts_by_ph[b.placeholder]
            metric = metrics_by_ph[b.placeholder]
            if fact.value_shape == "text":
                payload = fact.value if isinstance(fact.value, dict) else {}
                context[b.placeholder] = str(payload.get("text", ""))
            elif fact.value_shape in ("series", "pie"):
                if image_factory is None:
                    raise PreviewError(
                        f"变量 {b.placeholder} 绑定的指标类型为 {fact.value_shape}，"
                        "但未提供图表渲染器"
                    )
                context[b.placeholder] = image_factory(fact, metric)
            elif isinstance(fact.value, dict):
                raise PreviewError(
                    f"变量 {b.placeholder} 的指标类型 {fact.value_shape} 无法渲染到 Word"
                )
            else:
                # 展示口径：换算后的值 + 展示单位（未配置换算时与升级前完全一致）
                context[b.placeholder] = format_scalar_display(fact, metric)
        elif b.binding_type == "insight":
            # 槽位 key 缺省即 placeholder 名 -> 一个 AI 占位符 = 一段独立摘要
            slot = b.binding_key or b.placeholder
            context[b.placeholder] = insight_texts.get(slot, AI_SUMMARY_PLACEHOLDER)
        else:
            raise PreviewError(
                f"变量 {b.placeholder} 的绑定类型非法: {b.binding_type}"
            )
    return context


async def render_preview(
    db: AsyncSession,
    version: TemplateVersion,
    period: str,
) -> Path:
    """渲染预览文件（insight 槽位用占位文本），返回输出路径。"""
    bindings, facts_by_ph, metrics_by_ph = await compute_template_facts(
        db, version, period
    )
    settings = get_settings()
    output = (
        Path(settings.storage_dir)
        / "previews"
        / f"preview_{version.template_id}_{period}_{uuid.uuid4().hex[:8]}.docx"
    )
    tpl_path = Path(version.file_path)
    try:
        # 图表需要 DocxTemplate 实例来构造 InlineImage；无图表时也走同一模式，
        # 避免"一处传 tpl 一处不传"的不一致（见 make_image_factory 注释）
        tpl = open_template(tpl_path)
        context = build_context(
            bindings,
            facts_by_ph,
            metrics_by_ph,
            {},
            image_factory=make_image_factory(tpl),
        )
        return render_docx(tpl_path, context, output, tpl=tpl)
    except RenderError as exc:
        # 渲染时变量列表以 docxtpl 为准，与上传时解析不一致需重新上传
        raise PreviewError(
            f"模板变量与上传时解析结果不一致，请重新上传模板: {exc}"
        ) from None
