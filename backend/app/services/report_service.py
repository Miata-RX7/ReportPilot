"""报告实例状态机：生成 -> 待确认 -> 已确认 -> 推送。

状态（7 态）与允许动作：
    pending              claim
    generating           generate_ok / generate_fail
    pending_confirmation confirm / resend_confirmation
    confirmed            push
    pushed               （终态）
    push_failed          retry_push
    failed               regenerate

用"状态 -> 允许动作集合"而不是"from -> to"映射：push 这类动作有两个可能结果
（pushed / push_failed），动作集合表达更准确。
"""

import logging
import time
from typing import Any
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.metric import MetricDefinition
from app.models.report import PushRecord, Report, ReportAudit, ReportSummary
from app.models.template import TemplateBinding, TemplateVersion, WordTemplate
from app.models.user import User
from app.schemas.metric import MetricFact
from app.services import ai_insight, ai_trace, template_service
from app.services.ai_insight import InsightConfig
from app.services.docx_renderer import RenderError, open_template, render_docx
from app.services.mailer import (
    MailerError,
    send_confirmation_email,
    send_reminder_email,
    send_report_email,
)
from app.services.preview_service import (
    summary_placeholders_of,
    PreviewError,
    build_context,
    collect_facts,
    compute_template_facts,
    make_image_factory,
)
from app.services.task_service import (
    confirmation_url,
    hash_token,
    new_confirmation_token,
    token_matches,
)

logger = logging.getLogger(__name__)

# 收件人上限（防止误配超大收件列表）
MAX_RECIPIENTS = 20

ALLOWED_ACTIONS: dict[str, set[str]] = {
    "pending": {"claim"},
    "generating": {"generate_ok", "generate_fail"},
    "pending_confirmation": {
            "confirm",
            # 「仅确认不发送」（2026-11-12）：确认人线下自己处理了，
            # 告诉系统「这轮结束」—— 消费 token、不推送、不再催办
            "confirm_only",
            "confirm_by_admin",
            "resend_confirmation",
        },
    "confirmed": {"push"},
    # 「仅确认不发送」的终态：确认人线下自己处理了，系统不再发也不再催
    # （2026-11-12）。无后续动作 —— 不可撤销，有问题重新手动触发。
    "confirmed_no_push": set(),
    "pushed": set(),
    "push_failed": {"retry_push"},
    "failed": {"regenerate"},
}


class ReportError(Exception):
    """报告状态/参数错误。消息不含敏感信息。"""


def _aware(dt: datetime | None) -> datetime | None:
    """把 naive 时间当作 UTC 处理（SQLite 不带时区读回是 naive，PG 是 aware）。"""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _ensure_action(report: Report, action: str) -> None:
    if action not in ALLOWED_ACTIONS.get(report.status, set()):
        raise ReportError(f"状态 {report.status} 不允许 {action}")


def _audit(
    db: AsyncSession,
    report_id: int,
    actor_id: int | None,
    action: str,
    comment: str | None = None,
) -> None:
    db.add(
        ReportAudit(
            report_id=report_id,
            auditor_id=actor_id,
            action=action,
            comment=comment,
        )
    )


def _set_status(report: Report, target: str, action: str) -> None:
    """推进状态（调用前须已 _ensure_action）。"""
    _ensure_action(report, action)
    report.status = target
    if target in ("pending_confirmation", "confirmed", "pushed"):
        report.last_error = None
        report.error_stage = None
    if target == "pushed":
        report.pushed_at = datetime.now(timezone.utc)


def _arm_confirmation(report: Report) -> str:
    """写 token 哈希 / 发送时间 / 过期时间，重置提醒计时；返回明文 token。"""
    raw = new_confirmation_token()
    now = datetime.now(timezone.utc)
    report.confirmation_token = hash_token(raw)
    report.confirmation_sent_at = now
    report.expires_at = now + timedelta(hours=get_settings().confirm_token_ttl_hours)
    report.last_reminder_at = None
    return raw


async def report_display_name(db: AsyncSession, report: Report) -> str:
    """报告展示名：模板名_周期。"""
    tpl = await db.get(WordTemplate, report.template_id)
    return f"{tpl.name if tpl else f'模板#{report.template_id}'}_{report.period}"


# --------------------------------------------------------------------------
# 创建实例
# --------------------------------------------------------------------------
async def create_instance(
    db: AsyncSession,
    *,
    template: WordTemplate,
    version: TemplateVersion,
    period: str,
    recipient_emails: list[str],
    confirmer_email: str,
    source: str = "manual",
    task_id: int | None = None,
    scheduled_for: datetime | None = None,
    actor_id: int | None = None,
) -> Report:
    """创建 pending 实例（不落文件、不计算）。调度器与手动创建共用。"""
    # 去重保序：前端 tag 输入会去重，但 API 可能被直接调用
    recipients: list[str] = []
    for x in recipient_emails or []:
        s_ = str(x).strip()
        if s_ and s_ not in recipients:
            recipients.append(s_)
    if not recipients:
        raise ReportError("收件人至少需要 1 个邮箱")
    if len(recipients) > MAX_RECIPIENTS:
        raise ReportError(f"收件人最多 {MAX_RECIPIENTS} 个")
    confirmer = str(confirmer_email or "").strip()
    if not confirmer:
        raise ReportError("确认人邮箱必填")
    report = Report(
        task_id=task_id,
        template_id=template.id,
        version_id=version.id,
        source=source,
        scheduled_for=scheduled_for,
        period=period,
        status="pending",
        recipient_emails=recipients,
        confirmer_email=confirmer,
        created_by=actor_id,
    )
    db.add(report)
    await db.flush()
    _audit(db, report.id, actor_id, "create", f"创建实例 period={period} source={source}")
    logger.info(
        "实例已创建 id=%d task=%s source=%s period=%s",
        report.id,
        task_id,
        source,
        period,
    )
    return report


# --------------------------------------------------------------------------
# 生成
# --------------------------------------------------------------------------
async def _resolve_version(
    db: AsyncSession, report: Report
) -> tuple[WordTemplate, TemplateVersion]:
    template = await db.get(WordTemplate, report.template_id)
    if template is None or not template.is_active:
        raise ReportError("模板不存在或已停用，无法生成")
    version = (
        await db.get(TemplateVersion, report.version_id)
        if report.version_id is not None
        else None
    )
    if version is None:
        version = await template_service.get_current_version(db, template)
    if version is None:
        raise ReportError("模板无可用版本，无法生成")
    report.version_id = version.id
    return template, version


async def _render_report(
    db: AsyncSession,
    report: Report,
    version: TemplateVersion,
    *,
    actor_id: int | None = None,
) -> tuple[list[MetricFact], dict[str, dict]]:
    """生成核心：计算 facts -> 逐槽位 AI 摘要 -> 渲染 docx。"""
    logger.info(
        "报告生成开始 id=%d template=%d version=%d period=%s",
        report.id,
        version.template_id,
        version.version_no,
        report.period,
    )
    bindings, facts_by_ph, metrics_by_ph = await compute_template_facts(
        db, version, report.period
    )
    facts_list = collect_facts(facts_by_ph)
    logger.info("指标计算完成 id=%d count=%d", report.id, len(facts_list))

    insights = await generate_insights(
        bindings,
        facts_list,
        report.period,
        report_id=report.id,
        actor_id=actor_id,
    )
    logger.info("AI 摘要生成完成 id=%d slots=%d", report.id, len(insights))

    render_report_docx(
        report,
        version,
        bindings,
        facts_by_ph,
        metrics_by_ph,
        {slot: data["text"] for slot, data in insights.items()},
    )
    return facts_list, insights


async def generate_insights(
    bindings: list[TemplateBinding],
    facts: list[MetricFact],
    period: str,
    *,
    report_id: int | None = None,
    actor_id: int | None = None,
) -> dict[str, dict]:
    """为每个「AI 摘要」绑定各生成一段摘要（槽位 key -> 结果 dict）。

    每段独立使用自己绑定的提示词/参考范文；单段失败各自降级为占位文本，
    不影响其它段，也不阻塞整篇报告生成。

    report_id / actor_id 只用于 AI 调用留痕（哪份报告的哪一段、谁触发的）。
    """
    insights: dict[str, dict] = {}
    for binding in bindings:
        if binding.binding_type != "insight":
            continue
        slot = binding.binding_key or binding.placeholder
        config = InsightConfig.from_mapping(binding.ai_config_json)
        result = await ai_insight.generate_summary(
            facts,
            period,
            config,
            trace=ai_trace.AiCallContext(
                kind="generate",
                report_id=report_id,
                slot=slot,
                period=period,
                actor_id=actor_id,
            ),
        )
        insights[slot] = result
        logger.info(
            "AI 摘要槽位完成 slot=%s degraded=%s custom=%s unverified=%d",
            slot,
            result["degraded"],
            config.has_custom,
            len(result["unverified_numbers"]),
        )
    return insights


def render_report_docx(
    report: Report,
    version: TemplateVersion,
    bindings: list[TemplateBinding],
    facts_by_ph: dict[str, MetricFact],
    metrics_by_ph: dict[str, MetricDefinition],
    insight_texts: dict[str, str],
) -> Path:
    """把 facts + 各段摘要文本渲染成报告 docx（不取数、不调 AI）。

    生成链路与「单段摘要重生成」共用：后者用报告里的 facts 快照重建
    facts_by_ph / metrics_by_ph 后调用本函数，避免重算指标。
    """
    output = (
        Path(get_settings().storage_dir)
        / "reports"
        / f"report_{report.id}_{report.period}.docx"
    )
    tpl_path = Path(version.file_path)
    try:
        tpl = open_template(tpl_path)
        context = build_context(
            bindings,
            facts_by_ph,
            metrics_by_ph,
            insight_texts,
            image_factory=make_image_factory(tpl),
            template_summary_placeholders=summary_placeholders_of(tpl),
        )
        render_docx(tpl_path, context, output, tpl=tpl)
    except RenderError as exc:
        raise PreviewError(f"模板渲染失败: {exc}") from None
    report.file_path = str(output)
    return output


async def run_generation(report_id: int, *, actor_id: int | None = None) -> str | None:
    """执行生成并推进状态。返回确认链接（失败时 None）。

    独立 session，供调度器线程池调用：
      pending -> generating -> pending_confirmation（成功）/ failed（失败）
    """
    raw_token: str | None = None
    async with AsyncSessionLocal() as db:
        report = await db.get(Report, report_id)
        if report is None:
            logger.warning("报告不存在 id=%d", report_id)
            return None
        try:
            _set_status(report, "generating", "claim")
            _audit(db, report.id, actor_id, "generate")
            await db.commit()

            _, version = await _resolve_version(db, report)
            facts_list, insights = await _render_report(
                db, report, version, actor_id=actor_id
            )
            report.facts_json = [f.model_dump() for f in facts_list]
            report.insights_json = insights
            _set_status(report, "pending_confirmation", "generate_ok")
            raw_token = _arm_confirmation(report)
            _audit(db, report.id, actor_id, "generate_ok", "生成完成，待确认")
            await db.commit()
            logger.info("报告生成完成 id=%d 待确认", report.id)
        except Exception as exc:
            await db.rollback()
            await _mark_generation_failed(report_id, exc, actor_id)
            return None

    # 报告正文转 PDF（确认页预览）。失败不影响报告生成 —— 只是少了预览。
    try:
        await convert_report_pdf(report_id)
    except Exception:
        logger.exception("PDF 转换异常（不影响报告）report_id=%d", report_id)

    # 邮件在事务外发送：SMTP 失败不影响实例状态（用户可"重新发送"）
    if raw_token:
        try:
            await _deliver_confirmation_mail(report_id, raw_token, reminder=False)
        except Exception as exc:
            logger.error("确认邮件发送失败 report=%d: %s", report_id, exc)
    return raw_token


async def convert_report_pdf(report_id: int) -> str | None:
    """把报告 DOCX 转成 PDF 并记到 ``report.pdf_path``（确认页预览用）。

    转不动**不报错** —— 报告该生成还是生成，只是确认页少了 PDF 预览
    （还能下载 Word）。
    """
    from app.services.pdf_converter import convert_docx_to_pdf

    async with AsyncSessionLocal() as db:
        report = await db.get(Report, report_id)
        if report is None:
            return None
        docx_path = Path(report.file_path) if report.file_path else None
        if docx_path is None or not docx_path.exists():
            return None
        out_dir = docx_path.parent
    pdf_path = await convert_docx_to_pdf(docx_path, out_dir)
    if pdf_path is None:
        return None
    async with AsyncSessionLocal() as db:
        report = await db.get(Report, report_id)
        if report is not None:
            report.pdf_path = str(pdf_path)
            await db.commit()
    return str(pdf_path)


async def _mark_generation_failed(
    report_id: int, exc: Exception, actor_id: int | None
) -> None:
    async with AsyncSessionLocal() as db:
        report = await db.get(Report, report_id)
        if report is None:
            return
        report.status = "failed"
        report.last_error = str(exc)[:2000]
        report.error_stage = "generate"
        _audit(db, report.id, actor_id, "generate_fail", f"生成失败: {exc}")
        await db.commit()
        logger.error("报告生成失败 id=%d: %s", report_id, exc)


async def regenerate(report_id: int, *, actor_id: int | None = None) -> None:
    """failed -> generating 重跑（同一实例重算，覆盖产物）。"""
    async with AsyncSessionLocal() as db:
        report = await db.get(Report, report_id)
        if report is None:
            raise ReportError("报告不存在")
        report.confirmation_token = None
        report.confirmation_sent_at = None
        report.expires_at = None
        report.last_reminder_at = None
        _set_status(report, "generating", "regenerate")
        _audit(db, report.id, actor_id, "regenerate", "重新生成")
        await db.commit()
    await run_generation(report_id, actor_id=actor_id)


# --------------------------------------------------------------------------
# AI 摘要单段重生成
# --------------------------------------------------------------------------
def _find_insight_binding(
    bindings: list[TemplateBinding], slot: str
) -> TemplateBinding | None:
    """按槽位 key 找 insight 绑定。

    找不到时，**仅当该版本只有一个 AI 摘要槽位**才回落（兼容升级前的
    固定槽位 "summary" 历史报告）；多槽位场景必须精确匹配，避免改错段。
    """
    insight_bindings = [b for b in bindings if b.binding_type == "insight"]
    for binding in insight_bindings:
        if (binding.binding_key or binding.placeholder) == slot:
            return binding
    if len(insight_bindings) == 1:
        return insight_bindings[0]
    return None


async def _render_maps_from_snapshot(
    db: AsyncSession, bindings: list[TemplateBinding], facts: list[MetricFact]
) -> tuple[dict[str, MetricFact], dict[str, MetricDefinition]]:
    """用报告里的 facts 快照重建 (placeholder -> fact, placeholder -> metric)。

    重渲染不重算指标。若快照中缺失某绑定所需指标（模板换版本 / 指标被删），
    明确报错提示改用「重新生成整篇报告」，避免渲染出缺数报告。
    """
    by_metric = {f.metric_id: f for f in facts}
    metric_ids = {
        b.binding_ref_id
        for b in bindings
        if b.binding_type == "metric" and b.binding_ref_id is not None
    }
    metrics_by_id: dict[int, MetricDefinition] = {}
    if metric_ids:
        rows = await db.scalars(
            select(MetricDefinition).where(MetricDefinition.id.in_(metric_ids))
        )
        metrics_by_id = {m.id: m for m in rows}

    facts_by_ph: dict[str, MetricFact] = {}
    metrics_by_ph: dict[str, MetricDefinition] = {}
    for binding in bindings:
        if binding.binding_type != "metric" or binding.binding_ref_id is None:
            continue
        fact = by_metric.get(binding.binding_ref_id)
        metric = metrics_by_id.get(binding.binding_ref_id)
        if fact is None or metric is None:
            raise ReportError(
                f"变量 {binding.placeholder} 绑定的指标不在本报告事实快照中，"
                "请改用「重新生成整篇报告」"
            )
        facts_by_ph[binding.placeholder] = fact
        metrics_by_ph[binding.placeholder] = metric
    return facts_by_ph, metrics_by_ph


async def regenerate_insight(
    report_id: int,
    slot: str,
    *,
    config_override: dict | None = None,
    actor_id: int | None = None,
) -> dict:
    """只重算某一段 AI 摘要并重渲染 docx，返回该槽位结果。

    - facts 直接取报告快照 report.facts_json，**不重算指标**（快、且与首发一致）
    - 配置默认取模板当前绑定，允许 config_override 临时覆盖（页面改完提示词
      先试再存，无需保存模板）
    - 不改报告状态：报告已在待确认/已确认/已推送，重生成摘要只是替换文字
    - 渲染失败不写库：避免出现"文字已更新但 docx 还是旧的"这种不一致
    """
    async with AsyncSessionLocal() as db:
        report = await db.get(Report, report_id)
        if report is None:
            raise ReportError("报告不存在")
        if not report.facts_json:
            raise ReportError("该报告没有事实数据快照，无法单独重生成摘要")

        _, version = await _resolve_version(db, report)
        bindings = await template_service.bindings_for_version(db, version.id)
        binding = _find_insight_binding(bindings, slot)
        if binding is None:
            raise ReportError(f"当前模板版本没有 AI 摘要槽位: {slot}")

        raw: dict = dict(binding.ai_config_json or {})
        for key, value in (config_override or {}).items():
            if value is not None:
                raw[key] = value
        config = InsightConfig.from_mapping(raw)

        facts_list = [MetricFact.model_validate(item) for item in report.facts_json]

        # 以绑定上的槽位名为准写回：升级前的旧绑定固定用 "summary" 作 key，
        # 迁移后绑定 key 已归一到 placeholder 名，而历史报告的 insights_json
        # 仍是旧 key。若按请求传入的旧 key 写回，渲染时按新 key 取不到文本，
        # 会把这一段渲染成占位符 —— 因此这里顺手把旧 key 迁移掉。
        target = binding.binding_key or binding.placeholder
        summary = await ai_insight.generate_summary(
            facts_list,
            report.period,
            config,
            trace=ai_trace.AiCallContext(
                kind="regenerate",
                report_id=report.id,
                template_id=report.template_id,
                slot=target,
                period=report.period,
                actor_id=actor_id,
            ),
        )
        insights = dict(report.insights_json or {})
        if target != slot:
            insights.pop(slot, None)
        insights[target] = summary
        facts_by_ph, metrics_by_ph = await _render_maps_from_snapshot(
            db, bindings, facts_list
        )
        render_report_docx(
            report,
            version,
            bindings,
            facts_by_ph,
            metrics_by_ph,
            {s: (data.get("text") or "") for s, data in insights.items()},
        )
        report.insights_json = insights
        _audit(
            db,
            report.id,
            actor_id,
            "regenerate_insight",
            f"重生成 AI 摘要槽位 {target}（custom={config.has_custom}）",
        )
        await db.commit()
        logger.info("AI 摘要单段重生成完成 id=%d slot=%s", report_id, target)
        return {**summary, "slot": target}


# --------------------------------------------------------------------------
# 确认
# --------------------------------------------------------------------------
async def _get_locked(db: AsyncSession, report_id: int) -> Report:
    """取报告并加行锁（SQLite 不支持 FOR UPDATE，退化为普通查询）。"""
    dialect = db.bind.dialect.name if db.bind is not None else ""
    stmt = select(Report).where(Report.id == report_id)
    if dialect in ("postgresql", "mysql"):
        stmt = stmt.with_for_update()
    row = await db.scalar(stmt)
    if row is None:
        raise ReportError("报告不存在")
    return row


async def confirm(
    report_id: int,
    raw_token: str,
    summaries: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """凭一次性 token 确认并立即推送。

    返回 (推送结果标识, 错误信息)；结果标识 ∈ pushed / push_failed。

    - SELECT ... FOR UPDATE 锁定行，并发点击只有一个成功（Q11）
    - token 不匹配 / 已使用 -> 明确报错，不静默失败
    - ``summaries``：确认人最终提交的总结（人工编辑后的文本）。**快照**落库 ——
      之后文件被替换也不影响本次报告的内容（2026-11-03）
    """
    async with AsyncSessionLocal() as db:
        report = await _get_locked(db, report_id)
        if report.status != "pending_confirmation":
            if report.status in ("confirmed", "pushed"):
                raise ReportError("该链接已使用")
            raise ReportError(f"状态 {report.status} 不允许确认")
        if not token_matches(raw_token, report.confirmation_token):
            if report.confirmation_token is None:
                raise ReportError("该链接已使用")
            raise ReportError("确认链接无效")
        expires = _aware(report.expires_at)
        if expires is not None and expires < datetime.now(timezone.utc):
            raise ReportError("确认链接已过期，请联系管理员重新发送")

        # 总结快照（必须在清 token 之前写好：同一事务，失败一起回滚）
        if summaries:
            await _save_summary_snapshots(db, report, summaries)

        report.confirmation_token = None  # 一次性：确认后立即失效
        report.confirmed_at = datetime.now(timezone.utc)
        _set_status(report, "confirmed", "confirm")
        _audit(
            db,
            report.id,
            None,
            "confirm",
            f"确认人 {report.confirmer_email} 已确认",
        )
        await db.commit()

    # 有总结快照时**重渲染**报告：把总结填进「总结_」占位后再推送
    if summaries:
        try:
            await rebuild_report_with_summaries(report_id)
            # 重渲染覆盖了 DOCX -> PDF 预览要跟着更新（否则预览与最终发出的不一致）
            await convert_report_pdf(report_id)
        except Exception:
            logger.exception("报告重渲染失败（总结未写入）report_id=%d", report_id)

    ok, err = await push_confirmed(report_id, actor_id=None, action="push")
    return ("pushed" if ok else "push_failed"), err


async def confirm_only(
    report_id: int,
    raw_token: str,
    summaries: list[dict[str, Any]] | None = None,
) -> None:
    """**仅确认，不发送** —— 本轮报告自动化任务就此结束。

    场景（2026-11-12）：确认人下载了报告、线下改完自己发给收件人了，
    系统不该再发一次、也不该继续催办。点这个按钮就是告诉系统「我处理完了」。

    与 :func:`confirm` 的区别：
    - **不推送**（状态落在终态 ``confirmed_no_push``，推送逻辑不会碰它）
    - 依然**消费一次性 token**（链接用掉就不能再确认）
    - **不可撤销**（用户明确要求：有问题重新手动触发即可）
    """
    async with AsyncSessionLocal() as db:
        report = await _get_locked(db, report_id)
        if report.status != "pending_confirmation":
            if report.status in ("confirmed", "pushed", "confirmed_no_push"):
                raise ReportError("该链接已使用")
            raise ReportError(f"状态 {report.status} 不允许确认")
        if not token_matches(raw_token, report.confirmation_token):
            if report.confirmation_token is None:
                raise ReportError("该链接已使用")
            raise ReportError("确认链接无效")
        expires = _aware(report.expires_at)
        if expires is not None and expires < datetime.now(timezone.utc):
            raise ReportError("确认链接已过期，请联系管理员重新发送")

        if summaries:
            await _save_summary_snapshots(db, report, summaries)

        report.confirmation_token = None  # 一次性
        report.confirmed_at = datetime.now(timezone.utc)
        report.confirmed_no_push_at = datetime.now(timezone.utc)
        _set_status(report, "confirmed_no_push", "confirm_only")
        _audit(
            db,
            report.id,
            None,
            "confirm_only",
            f"确认人 {report.confirmer_email} 已确认（未通过系统发送）",
        )
        await db.commit()
    logger.info("报告已确认但不发送 report_id=%d", report_id)


async def _save_summary_snapshots(
    db: AsyncSession, report: Report, summaries: list[dict[str, Any]]
) -> None:
    """把确认人提交的总结写成快照。"""
    from app.services import report_summary_service

    files = {f.id: f for f in await report_summary_service.list_files(db, report.id)}
    for s in summaries:
        item = files.get(int(s.get("file_id") or 0))
        if item is None:
            logger.warning("确认提交的总结找不到对应文件，跳过 report_id=%d", report.id)
            continue
        await report_summary_service.save_summary(
            db,
            report,
            item,
            placeholder=str(s.get("placeholder") or ""),
            prompt=str(s.get("prompt") or ""),
            text=str(s.get("text") or ""),
            draft=None,
        )
        # 溯源与校验结果直接沿用确认页传来的（那是最终版本）
        row = await db.scalar(
            select(ReportSummary).where(
                ReportSummary.report_id == report.id,
                ReportSummary.placeholder == str(s.get("placeholder") or ""),
            )
        )
        if row is not None:
            row.categories_json = list(s.get("categories") or [])
            row.verification_json = dict(s.get("verification") or {})


async def rebuild_report_with_summaries(report_id: int) -> Path:
    """用总结快照重渲染报告 docx（覆盖 report.file_path）。

    「总结_」占位在确认前是空的，确认后才有内容 —— 所以要重渲染一次。
    """
    from app.services import report_summary_service

    async with AsyncSessionLocal() as db:
        report = await db.get(Report, report_id)
        if report is None:
            raise ReportError("报告不存在")
        template = await db.get(WordTemplate, report.template_id)
        version = await db.get(TemplateVersion, report.version_id) if report.version_id else None
        if template is None or version is None:
            raise ReportError("报告模板或版本缺失，无法重渲染")
        texts = await report_summary_service.summary_texts(db, report_id)
        bindings, facts_by_ph, metrics_by_ph = await compute_template_facts(
            db, version, report.period
        )
        # insight 槽位的文本存在报告的 insights_json 里（key 已归一到 placeholder 名）
        insight_texts = {
            k: str(v.get("text") or "")
            for k, v in (report.insights_json or {}).items()
            if isinstance(v, dict)
        }
        output = Path(get_settings().storage_dir) / "reports" / f"report_{report.id}_{report.period}.docx"
        tpl = open_template(Path(version.file_path))
        context = build_context(
            bindings,
            facts_by_ph,
            metrics_by_ph,
            insight_texts,
            image_factory=make_image_factory(tpl),
            summary_texts=texts,
            template_summary_placeholders=summary_placeholders_of(tpl),
        )
        render_docx(Path(version.file_path), context, output, tpl=tpl)
        report.file_path = str(output)
        await db.commit()
        return output


async def confirm_by_user(report_id: int, *, actor_id: int) -> tuple[str, str]:
    """**确认人本人**在平台内确认（邮箱与任务的 confirmer_email 匹配）。

    与 confirm() 的区别只是不校验 token（凭登录态 + 邮箱匹配），
    过期链接同样允许本人确认——过期限制的是"链接"，不是"这个人的权限"。
    """
    async with AsyncSessionLocal() as db:
        report = await _get_locked(db, report_id)
        if report.status != "pending_confirmation":
            if report.status in ("confirmed", "pushed"):
                raise ReportError("该报告已确认，无需重复操作")
            raise ReportError(f"状态 {report.status} 不允许确认")
        user = await db.get(User, actor_id)
        if user is None or (user.email or "").lower() != (report.confirmer_email or "").lower():
            raise ReportError("只有该任务的确认人本人可以确认此报告")
        expired = False
        expires = _aware(report.expires_at)
        if expires is not None and expires < datetime.now(timezone.utc):
            expired = True
        report.confirmation_token = None
        report.confirmed_at = datetime.now(timezone.utc)
        _set_status(report, "confirmed", "confirm")
        _audit(
            db,
            report.id,
            actor_id,
            "confirm",
            f"确认人 {user.display_name or user.username} 在平台内确认"
            + ("（原确认链接已过期）" if expired else ""),
        )
        await db.commit()

    ok, err = await push_confirmed(report_id, actor_id=actor_id, action="push")
    return ("pushed" if ok else "push_failed"), err


async def confirm_by_admin(report_id: int, *, actor_id: int) -> tuple[str, str]:
    """管理员代确认（平台内操作，无需邮件链接）。

    用于确认人长期不点链接、或邮件通道异常时，管理员在平台上直接放行。
    与 confirm() 的区别：
    - 不校验 token（凭登录态 + 管理员权限）
    - 允许对**已过期**的链接代确认（过期只影响链接，不影响报告本身）
    - 审计写明"管理员代确认"并记录操作人，便于追溯
    """
    async with AsyncSessionLocal() as db:
        report = await _get_locked(db, report_id)
        if report.status != "pending_confirmation":
            if report.status in ("confirmed", "pushed"):
                raise ReportError("该报告已确认，无需重复操作")
            raise ReportError(f"状态 {report.status} 不允许确认")
        expired = False
        expires = _aware(report.expires_at)
        if expires is not None and expires < datetime.now(timezone.utc):
            expired = True
        report.confirmation_token = None
        report.confirmed_at = datetime.now(timezone.utc)
        _set_status(report, "confirmed", "confirm_by_admin")
        _audit(
            db,
            report.id,
            actor_id,
            "confirm_by_admin",
            "管理员代确认（原确认链接已过期）" if expired else "管理员代确认",
        )
        await db.commit()

    ok, err = await push_confirmed(report_id, actor_id=actor_id, action="push")
    return ("pushed" if ok else "push_failed"), err


async def resend_confirmation(
    report_id: int, *, actor_id: int | None = None
) -> None:
    """重新发送确认邮件（换新 token、重置提醒与过期时间）。"""
    async with AsyncSessionLocal() as db:
        report = await db.get(Report, report_id)
        if report is None:
            raise ReportError("报告不存在")
        _ensure_action(report, "resend_confirmation")
        raw = _arm_confirmation(report)
        _audit(db, report.id, actor_id, "resend_confirmation", "重新发送确认邮件")
        await db.commit()
    await _deliver_confirmation_mail(report_id, raw, reminder=False)


async def _deliver_confirmation_mail(
    report_id: int, raw_token: str, *, reminder: bool
) -> None:
    """发送确认/提醒邮件（token 明文只在邮件里出现），并写 PushRecord。

    与推送（push_confirmed）一致：**成功与失败都记**一条 PushRecord，
    action = confirmation / reminder，收件人 = 确认人，无附件。
    以前这里不落记录，于是"用户确实收到了邮件，但推送记录页一片空白"。
    失败时把异常重新抛出，由调用方决定是否吞掉（生成流程已有兜底）。
    """
    from app.services.mailer import send_confirmation_email, send_reminder_email

    async with AsyncSessionLocal() as db:
        report = await db.get(Report, report_id)
        if report is None:
            return
        name = await report_display_name(db, report)
        to = report.confirmer_email or ""
        url = confirmation_url(report_id, raw_token)
        expires = _aware(report.expires_at)
        # 序号 = 该报告所有邮件记录数 + 1（确认信先于推送发出，故推送序号会从 2 起）
        attempt = await _next_attempt(db, report_id)
        started = time.perf_counter()
        err: str | None = None
        try:
            if reminder:
                await send_reminder_email(
                    to=to,
                    report_name=name,
                    period=report.period,
                    confirm_url=url,
                    expires_at=expires,
                )
            else:
                await send_confirmation_email(
                    to=to,
                    report_name=name,
                    period=report.period,
                    confirm_url=url,
                    expires_at=expires,
                )
        except MailerError as exc:
            err = str(exc)
        except Exception as exc:  # 兜底：邮件链路任何异常都要留下痕迹
            err = f"{type(exc).__name__}: {exc}"
        duration_ms = int((time.perf_counter() - started) * 1000)

        db.add(
            PushRecord(
                report_id=report_id,
                attempt=attempt,
                action="reminder" if reminder else "confirmation",
                status="failed" if err else "success",
                recipients=[to] if to else [],
                recipient_count=1 if to else 0,
                attachment_name=None,
                duration_ms=duration_ms,
                error=err[:2000] if err else None,
                actor_id=None,
            )
        )
        await db.commit()
        logger.info(
            "确认邮件发送完成 report=%d action=%s attempt=%d status=%s",
            report_id,
            "reminder" if reminder else "confirmation",
            attempt,
            "failed" if err else "success",
        )
        if err:
            raise MailerError(err)


async def send_reminder(report_id: int) -> None:
    """24h 未确认提醒（由 scheduler 的每小时 sweep 调用）。"""
    async with AsyncSessionLocal() as db:
        report = await db.get(Report, report_id)
        if report is None or not report.confirmation_token:
            return
        # 库里只有哈希，明文无法复原 -> 换新 token（同时保持"每次邮件都能点"）
        raw = _arm_confirmation_keep_reminder_time(report)
        await db.commit()
    await _deliver_confirmation_mail(report_id, raw, reminder=True)


def _arm_confirmation_keep_reminder_time(report: Report) -> str:
    """提醒场景：换新 token 但**不重置**过期时间（避免每次提醒都续命）。"""
    raw = new_confirmation_token()
    report.confirmation_token = hash_token(raw)
    return raw


# --------------------------------------------------------------------------
# 推送
# --------------------------------------------------------------------------
async def _next_attempt(db: AsyncSession, report_id: int) -> int:
    n = await db.scalar(
        select(func.count(PushRecord.id)).where(PushRecord.report_id == report_id)
    )
    return int(n or 0) + 1


async def push_confirmed(
    report_id: int,
    *,
    actor_id: int | None,
    action: str = "push",
    db: AsyncSession | None = None,
) -> tuple[bool, str]:
    """confirmed -> pushed / push_failed；push_failed 可重试。

    每次尝试（成功或失败）都写 PushRecord；失败不抛异常，回传 (False, err)。

    db 仅供测试注入（生产走默认短 session，避免长事务）。
    """
    if db is not None:
        return await _push_in_session(db, report_id, actor_id, action)
    async with AsyncSessionLocal() as session:
        return await _push_in_session(session, report_id, actor_id, action)


async def _push_in_session(
    db: AsyncSession, report_id: int, actor_id: int | None, action: str
) -> tuple[bool, str]:
        report = await db.get(Report, report_id)
        if report is None:
            return False, "报告不存在"
        if action not in ALLOWED_ACTIONS.get(report.status, set()):
            return False, f"状态 {report.status} 不允许 {action}"
        if not report.file_path or not Path(report.file_path).exists():
            err = "报告文件缺失，无法推送"
            report.status = "push_failed"
            report.last_error = err
            report.error_stage = "push"
            await db.commit()
            return False, err
        template = await db.get(WordTemplate, report.template_id)
        report_name = template.name if template else f"report_{report.id}"
        attachment = Path(report.file_path)
        recipients = list(report.recipient_emails or [])
        # 确认人上传的原始数据文件作为附加附件（明细在附件里，不进正文）
        from app.services import report_summary_service

        extra_attachments = [
            Path(f.stored_path)
            for f in await report_summary_service.list_files(db, report_id)
            if f.stored_path
        ]
        attempt = await _next_attempt(db, report_id)
        started = time.perf_counter()
        err: str | None = None
        try:
            await send_report_email(
                recipients,
                report_name,
                report.period,
                attachment,
                extra_attachments=extra_attachments,
            )
        except MailerError as exc:
            err = str(exc)
        duration_ms = int((time.perf_counter() - started) * 1000)

        if err is not None:
            report.status = "push_failed"
            report.last_error = err[:2000]
            report.error_stage = "push"
            _audit(db, report.id, actor_id, action, f"推送失败: {err}")
        else:
            _set_status(report, "pushed", action)
            _audit(db, report.id, actor_id, action, "推送成功")
        db.add(
            PushRecord(
                report_id=report_id,
                attempt=attempt,
                action=action,
                status="failed" if err else "success",
                recipients=recipients,
                recipient_count=len(recipients),
                attachment_name=attachment.name,
                duration_ms=duration_ms,
                error=err[:2000] if err else None,
                actor_id=actor_id,
            )
        )
        await db.commit()
        logger.info(
            "推送完成 report=%d attempt=%d status=%s",
            report_id,
            attempt,
            "failed" if err else "success",
        )
        return (err is None), (err or "")


async def retry_push(report_id: int, *, actor_id: int | None = None) -> Report:
    ok, err = await push_confirmed(report_id, actor_id=actor_id, action="retry_push")
    if not ok and err.startswith("状态"):
        raise ReportError(err)
    async with AsyncSessionLocal() as db:
        report = await db.get(Report, report_id)
        if report is None:
            raise ReportError("报告不存在")
        return report
