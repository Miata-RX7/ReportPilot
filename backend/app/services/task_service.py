"""报告任务服务：CRUD 校验 + 调度器同步 + 触发入口。

职责边界：
- task_service：任务的增删改查 + 校验 + 调度注册/重排
- scheduler：APScheduler 封装（进程内执行器，DB 是唯一真相源）
- report_service：实例的生成 / 确认 / 推送（状态机）
"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.report import Report
from app.models.task import ReportTask
from app.models.template import WordTemplate
from app.services.period_utils import (
    MAX_PERIOD_OFFSET,
    period_of_moment,
    site_tz,
)
from app.services.runtime_config import effective_settings

logger = logging.getLogger(__name__)

# 手动触发时 scheduled_for 用它与 cron 触发区分（cron 用整分/整点，不冲突）
MANUAL_SLOT_SUFFIX = " (manual)"


class TaskError(Exception):
    """任务配置/操作错误（消息可展示给用户）。"""


def task_job_id(task_id: int) -> str:
    """调度器 job id 规则（start/stop 用同一函数拼装）。"""
    return f"task:{task_id}"


# --------------------------------------------------------------------------
# 时区与 cron
# --------------------------------------------------------------------------
def report_tz():
    """报告任务的求值时区（薄封装：实现与周边界统一在 period_utils.site_tz）。"""
    return site_tz()


def build_cron_trigger(cron_expr: str) -> CronTrigger:
    """把 5 段式 cron 转成 APScheduler 触发器（带时区）。

    非法表达式抛 TaskError（调用方在校验阶段使用，校验失败不动调度器）。
    """
    expr = (cron_expr or "").strip()
    if not expr:
        raise TaskError("cron 表达式不能为空")
    try:
        return CronTrigger.from_crontab(expr, timezone=report_tz())
    except ValueError as exc:
        raise TaskError(f"cron 表达式非法: {exc}") from None


def next_fire_time(cron_expr: str, base: datetime | None = None) -> datetime:
    """按任务时区求下一次执行时间（注册后回写 next_run_at / 前端预览共用）。"""
    trigger = build_cron_trigger(cron_expr)
    base = base or datetime.now(timezone.utc)
    nxt = trigger.get_next_fire_time(None, base)
    return nxt.replace(tzinfo=timezone.utc) if nxt else None


def fmt_hk(dt: datetime | None) -> str:
    """按报告时区展示时间（邮件与文案统一口径）。"""
    if dt is None:
        return "-"
    return dt.astimezone(report_tz()).strftime("%Y-%m-%d %H:%M")


def period_of(moment: datetime) -> str:
    """触发时刻 -> 统计周期（月粒度，2026-10-09 -> 2026-10）。

    周粒度与偏移由 period_utils.period_of_moment 统一处理（任务配置驱动）。
    """
    return period_of_moment(moment)


# --------------------------------------------------------------------------
# 确认 token
# --------------------------------------------------------------------------
def new_confirmation_token() -> str:
    """生成一次性确认 token（明文进邮件，库里只存哈希）。"""
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def token_matches(raw: str, stored_hash: str | None) -> bool:
    """常量时间比较，避免时序侧信道。"""
    if not stored_hash:
        return False
    return hmac.compare_digest(hash_token(raw), stored_hash)


def confirmation_url(report_id: int, token: str) -> str:
    """确认链接：{APP_BASE_URL}/confirm/{id}?token=..."""
    base = effective_settings().app_base_url.rstrip("/")
    return f"{base}/confirm/{report_id}?token={token}"


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------
def validate_recipients(emails: list[str]) -> list[str]:
    """收件人：去空、去重、保序，至少 1 个（格式由 schema 的 EmailStr 保证）。"""
    out: list[str] = []
    for e in emails or []:
        s = str(e).strip()
        if s and s not in out:
            out.append(s)
    if not out:
        raise TaskError("收件人至少需要 1 个邮箱")
    return out


def validate_period_rule(granularity: str, offset: int) -> tuple[str, int]:
    """校验周期粒度与偏移，返回归一化后的 (granularity, offset)。

    - granularity：month（月报，默认）/ week（周报）
    - offset：0=触发时刻所在周期，-1=上一个周期（周一触发取上周、月初取上月）
    """
    g = str(granularity or "month").strip().lower()
    if g not in ("month", "week"):
        raise TaskError(f"周期粒度非法: {granularity!r}，应为 month 或 week")
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise TaskError(f"周期偏移非法: {offset!r}，应为整数")
    if offset > 0 or offset < -MAX_PERIOD_OFFSET:
        raise TaskError(
            f"周期偏移必须在 -{MAX_PERIOD_OFFSET} ~ 0 之间（0=本期，-1=上一周期）"
        )
    return g, offset


async def _get_template_or_error(db: AsyncSession, template_id: int) -> WordTemplate:
    tpl = await db.get(WordTemplate, template_id)
    if tpl is None:
        raise TaskError("模板不存在")
    if not tpl.is_active:
        raise TaskError("模板已停用，无法绑定到任务")
    return tpl


async def _assert_name_free(
    db: AsyncSession, name: str, *, exclude_id: int | None = None
) -> None:
    stmt = select(ReportTask.id).where(
        ReportTask.name == name, ReportTask.is_active.is_(True)
    )
    if exclude_id is not None:
        stmt = stmt.where(ReportTask.id != exclude_id)
    if await db.scalar(stmt) is not None:
        raise TaskError(f"启用中的任务已存在同名任务：{name}")


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------
async def create_task(
    db: AsyncSession,
    *,
    name: str,
    template_id: int,
    cron_expr: str,
    confirmer_email: str,
    recipient_emails: list[str],
    is_active: bool,
    actor_id: int,
    period_granularity: str = "month",
    period_offset: int = 0,
) -> ReportTask:
    """创建任务。**先校验 cron 再落库**；启用时同步注册调度。"""
    build_cron_trigger(cron_expr)  # 校验失败直接抛，不写库
    await _get_template_or_error(db, template_id)
    recipients = validate_recipients(recipient_emails)
    confirmer = str(confirmer_email or "").strip()
    if not confirmer:
        raise TaskError("确认人邮箱必填")
    granularity, offset = validate_period_rule(period_granularity, period_offset)

    # 查重必须在 db.add 之前：SQLAlchemy 会在下一条 SELECT 时 autoflush，
    # 若先 add，查重查询会把"自己"当成同名已存在，导致新建必然失败。
    if is_active:
        await _assert_name_free(db, name)

    task = ReportTask(
        name=name,
        template_id=template_id,
        cron_expr=cron_expr.strip(),
        confirmer_email=confirmer,
        recipient_emails=recipients,
        is_active=is_active,
        created_by=actor_id,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    if task.is_active:
        await sync_task_schedule(task)
    logger.info(
        "任务已创建 id=%d name=%s cron=%s active=%s",
        task.id,
        task.name,
        task.cron_expr,
        task.is_active,
    )
    return task


async def update_task(
    db: AsyncSession, task: ReportTask, payload: dict
) -> ReportTask:
    """更新任务。

    关键顺序（Q10）：**先校验新 cron，再动调度器**；校验失败时任务与调度都保持原样。
    """
    new_cron = (payload.get("cron_expr") or task.cron_expr).strip()
    build_cron_trigger(new_cron)  # 校验失败 -> 不改库、不动调度

    was_active = task.is_active
    if "name" in payload and payload["name"] != task.name:
        await _assert_name_free(db, payload["name"], exclude_id=task.id)
        task.name = payload["name"]
    if "template_id" in payload:
        await _get_template_or_error(db, int(payload["template_id"]))
        task.template_id = int(payload["template_id"])
    task.cron_expr = new_cron
    if "confirmer_email" in payload:
        c = str(payload["confirmer_email"] or "").strip()
        if not c:
            raise TaskError("确认人邮箱必填")
        task.confirmer_email = c
    if "recipient_emails" in payload:
        task.recipient_emails = validate_recipients(payload["recipient_emails"])
    if "period_granularity" in payload or "period_offset" in payload:
        granularity, offset = validate_period_rule(
            payload.get("period_granularity", task.period_granularity),
            payload.get("period_offset", task.period_offset),
        )
        task.period_granularity = granularity
        task.period_offset = offset
    if "is_active" in payload:
        task.is_active = bool(payload["is_active"])
        if task.is_active:
            await _assert_name_free(db, task.name, exclude_id=task.id)

    await db.commit()
    await db.refresh(task)

    # 调度同步：启用 -> reschedule；停用 -> 移除
    if task.is_active:
        await sync_task_schedule(task)
    else:
        await remove_task_schedule(task.id)
    if was_active and not task.is_active:
        logger.info("任务已停用 id=%d（历史实例保留）", task.id)
    return task


async def set_active(db: AsyncSession, task: ReportTask, is_active: bool) -> ReportTask:
    if is_active:
        build_cron_trigger(task.cron_expr)
        await _get_template_or_error(db, task.template_id)
        await _assert_name_free(db, task.name, exclude_id=task.id)
    task.is_active = is_active
    await db.commit()
    await db.refresh(task)
    if is_active:
        await sync_task_schedule(task)
    else:
        await remove_task_schedule(task.id)
    return task


async def delete_task(db: AsyncSession, task: ReportTask) -> None:
    """删除任务。

    硬约束（Q6）：仅允许删除 **已停用 且 从未执行过** 的任务，
    以保住实例的审计链（有实例的任务只能停用）。
    """
    if task.is_active:
        raise TaskError("请先停用任务再删除")
    if task.last_run_at is not None:
        raise TaskError("该任务已执行过，不能删除（可停用）")
    count = await db.scalar(
        select(func.count(Report.id)).where(Report.task_id == task.id)
    )
    if count:
        raise TaskError("该任务已产生实例，不能删除（可停用）")
    from app.services import scheduler as scheduler_mod

    await remove_task_schedule(task.id)
    await db.delete(task)
    await db.commit()
    logger.info("任务已删除 id=%d", task.id)


# --------------------------------------------------------------------------
# 调度同步（薄封装，真实实现放 scheduler 模块，避免循环依赖）
# --------------------------------------------------------------------------
async def sync_task_schedule(task: ReportTask) -> None:
    from app.services import scheduler as scheduler_mod

    try:
        nxt = await scheduler_mod.register_task_job(task.id, task.cron_expr)
    except TaskError:
        raise
    except Exception as exc:  # 调度器异常不应让任务保存失败，但要留痕
        logger.error("任务调度注册失败 id=%d: %s", task.id, exc)
        nxt = None
    await _write_next_run(task, nxt)


async def remove_task_schedule(task_id: int) -> None:
    from app.services import scheduler as scheduler_mod

    try:
        await scheduler_mod.unregister_task_job(task_id)
    except Exception as exc:
        logger.error("任务调度移除失败 id=%d: %s", task_id, exc)


async def _write_next_run(task: ReportTask, nxt: datetime | None) -> None:
    db = AsyncSessionLocal()
    try:
        row = await db.get(ReportTask, task.id)
        if row is not None:
            row.next_run_at = nxt
            await db.commit()
    finally:
        await db.close()
