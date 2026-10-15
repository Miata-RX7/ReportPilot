"""APScheduler 封装（进程内执行器，DB 是唯一真相源）。

三个关键工程约束（设计评审确认）：
1. **不阻塞主事件循环**：生成耗时 30s~2min，若在 AsyncIOScheduler 里直接 await
   协程会把 FastAPI 主循环卡住。因此 job 入口是**同步函数**，跑在线程池里，
   内部用 asyncio.run() 起独立事件循环执行异步生成逻辑。
2. **重复触发双保险**：认领时先拿 pg_try_advisory_xact_lock（短事务），再靠
   唯一索引 uq_report_task_slot 兜底（重启/多副本/misfire 场景）。
3. **多副本**：用 SCHEDULER_ENABLED 只在一个副本开启调度器（见 README）。
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.report import Report
from app.models.task import ReportTask
from app.services.period_utils import period_of_moment
from app.services.task_service import (
    TaskError,
    build_cron_trigger,
    report_tz,
    task_job_id,
)

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

# advisory lock 命名空间（与 task_id 组成两段 int4 key）
LOCK_NAMESPACE = 0x5245  # "RE"
# 系统级 job id
JOB_REMINDER = "system:reminder"
JOB_PUSH_COMPENSATION = "system:push-compensation"


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler


def scheduler_status() -> dict[str, object]:
    """调度器运行状态（供系统设置页只读展示）。"""
    if _scheduler is None:
        return {"running": False, "jobs": []}
    try:
        jobs = [j.id for j in _scheduler.get_jobs()]
    except Exception:  # pragma: no cover - 调度器内部异常不应影响接口
        jobs = []
    return {"running": bool(_scheduler.running), "jobs": jobs}


async def start_scheduler() -> None:
    """启动调度器：注册全部启用任务 + 两个系统 sweep。"""
    global _scheduler
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("SCHEDULER_ENABLED=false，跳过调度器启动（可作为纯 API 副本）")
        return

    _scheduler = AsyncIOScheduler(
        timezone=report_tz(),
        # job 入口是同步函数 -> 丢到线程池，避免阻塞主循环
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )
    _scheduler.start()
    logger.info("调度器已启动 timezone=%s", get_settings().report_timezone)

    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        tasks = list(
            (
                await db.scalars(
                    select(ReportTask).where(ReportTask.is_active.is_(True))
                )
            ).all()
        )
    for t in tasks:
        try:
            await register_task_job(t.id, t.cron_expr)
        except Exception as exc:
            logger.error("任务调度注册失败 id=%d cron=%r: %s", t.id, t.cron_expr, exc)

    # 每小时的系统 sweep：未确认提醒 + 推送补偿
    hourly = CronTrigger(minute=17, timezone=report_tz())  # 避开整点，减少与其它任务撞点
    _scheduler.add_job(
        _job_reminders,
        trigger=hourly,
        id=JOB_REMINDER,
        replace_existing=True,
    )
    _scheduler.add_job(
        _job_push_compensation,
        trigger=hourly,
        id=JOB_PUSH_COMPENSATION,
        replace_existing=True,
    )
    logger.info("已加载 %d 个启用任务 + 2 个系统任务", len(tasks))


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception as exc:
            logger.warning("调度器关闭异常: %s", exc)
        _scheduler = None
        logger.info("调度器已停止")


async def register_task_job(task_id: int, cron_expr: str) -> datetime | None:
    """注册/重排任务 job，并回写 next_run_at。调度器未启用时静默跳过。"""
    if _scheduler is None:
        return None
    trigger = build_cron_trigger(cron_expr)
    job = _scheduler.add_job(
        _job_run_task,
        trigger=trigger,
        args=[task_id],
        id=task_job_id(task_id),
        replace_existing=True,
    )
    nxt = getattr(job, "next_run_time", None)
    logger.info("任务 job 已注册 id=%d cron=%s next=%s", task_id, cron_expr, nxt)
    return nxt


async def unregister_task_job(task_id: int) -> None:
    if _scheduler is None:
        return
    try:
        _scheduler.remove_job(task_job_id(task_id))
        logger.info("任务 job 已移除 id=%d", task_id)
    except Exception as exc:
        logger.warning("移除任务 job 失败 id=%d: %s", task_id, exc)


# --------------------------------------------------------------------------
# job 入口（同步函数：跑在 APScheduler 线程池里，内部起独立事件循环）
# --------------------------------------------------------------------------
def _job_run_task(task_id: int) -> None:
    """cron 触发入口。线程池里执行，不阻塞主循环。"""
    try:
        asyncio.run(run_task_instance(task_id, source="cron"))
    except Exception as exc:
        logger.exception("任务执行异常 id=%d: %s", task_id, exc)


def _job_reminders() -> None:
    try:
        asyncio.run(sweep_reminders())
    except Exception as exc:
        logger.exception("提醒 sweep 异常: %s", exc)


def _job_push_compensation() -> None:
    try:
        asyncio.run(sweep_push_compensation())
    except Exception as exc:
        logger.exception("推送补偿 sweep 异常: %s", exc)


# --------------------------------------------------------------------------
# 触发执行：认领 -> 生成 -> 发确认邮件
# --------------------------------------------------------------------------
async def run_task_instance(
    task_id: int, *, source: str = "cron", period_override: str | None = None
) -> int | None:
    """执行一次任务。返回实例 id；被去重/锁占用时返回 None。

    流程（设计说明 §5）：
      短事务认领（advisory lock + 唯一索引）-> pending -> generating
      -> 复用生成核心 -> 成功发确认邮件 / 失败置 failed
    """
    from app.services import report_service

    async with AsyncSessionLocal() as db:
        task = await db.get(ReportTask, task_id)
        if task is None:
            logger.warning("任务不存在 id=%d", task_id)
            return None
        if not task.is_active and source == "cron":
            logger.info("任务已停用，跳过触发 id=%d", task_id)
            return None

        # ---- 认领：短事务 + advisory lock + 唯一索引 ----
        # 用隐式事务（前面的 db.get 已开启），commit 即释放 xact 锁；
        # 不要用 db.begin()，那会与已开启的隐式事务冲突。
        now = datetime.now(timezone.utc)
        slot = now
        try:
            locked = await db.scalar(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": LOCK_NAMESPACE, "key": task_id},
            )
            if not locked:
                logger.info("任务已被其它实例认领，跳过 id=%d", task_id)
                return None
            exist = await db.scalar(
                text(
                    "SELECT id FROM reports WHERE task_id = :t AND scheduled_for = :s LIMIT 1"
                ),
                {"t": task_id, "s": slot},
            )
            if exist:
                logger.info("该触发时刻已有实例，跳过 id=%d slot=%s", task_id, slot)
                return None
            report = Report(
                task_id=task_id,
                template_id=task.template_id,
                source=source,
                scheduled_for=slot,
                period=period_override
                or _period_of(
                    slot,
                    granularity=task.period_granularity,
                    offset=task.period_offset,
                ),
                status="pending",
                recipient_emails=list(task.recipient_emails),
                confirmer_email=task.confirmer_email,
                created_by=None,  # 定时触发无用户
            )
            db.add(report)
            await db.flush()
            report_id = report.id
            task.last_run_at = now
            await db.commit()
        except Exception as exc:
            await db.rollback()
            # 唯一索引冲突 = 并发重复触发
            if "uq_report_task_slot" in str(exc) or "unique" in str(exc).lower():
                logger.info("并发重复触发已被唯一索引挡住 id=%d", task_id)
                return None
            logger.exception("任务认领失败 id=%d: %s", task_id, exc)
            raise

    logger.info("实例已创建 report=%d task=%d source=%s", report_id, task_id, source)

    # ---- 执行生成（长事务之外）----
    await report_service.run_generation(report_id, actor_id=None)
    return report_id


def _period_of(moment: datetime, *, granularity: str, offset: int) -> str:
    """触发时刻 -> 统计周期（按任务粒度/偏移，站点时区）。

    granularity=month 且 offset=0 时结果与升级前完全一致（"2026-10"）。
    """
    return period_of_moment(moment, granularity, offset)


# --------------------------------------------------------------------------
# 系统 sweep
# --------------------------------------------------------------------------
async def sweep_reminders() -> int:
    """未确认提醒：pending_confirmation 且未过期且距上次提醒 >= 间隔。"""
    from app.services import report_service

    settings = get_settings()
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        # 用 text() 查询时结果是 Row/标量，取 id 用索引而不是 ORM 属性
        rows = (
            await db.execute(
                text(
                    "SELECT id FROM reports WHERE status = 'pending_confirmation' "
                    "AND confirmation_token IS NOT NULL "
                    "AND (expires_at IS NULL OR expires_at > :now) "
                    "AND (last_reminder_at IS NULL OR last_reminder_at <= :since) "
                    "ORDER BY id LIMIT 50"
                ),
                {
                    "now": now,
                    "since": now
                    - timedelta(hours=settings.reminder_interval_hours),
                },
            )
        ).all()
    ids = [int(r[0]) for r in rows]
    sent = 0
    for rid in ids:
        try:
            await report_service.send_reminder(rid)
            sent += 1
        except Exception as exc:
            logger.error("提醒发送失败 report=%d: %s", rid, exc)
    if sent:
        logger.info("提醒 sweep 完成，发送 %d 封", sent)
    return sent


async def sweep_push_compensation() -> int:
    """推送补偿：confirmed 但长时间没推出去的（防"确认后进程崩溃"卡死）。"""
    from app.services import report_service

    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=10)
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id FROM reports WHERE status = 'confirmed' "
                    "AND confirmed_at IS NOT NULL AND confirmed_at <= :since "
                    "ORDER BY id LIMIT 20"
                ),
                {"since": since},
            )
        ).all()
    ids = [int(r[0]) for r in rows]
    done = 0
    for rid in ids:
        try:
            await report_service.push_confirmed(rid, actor_id=None, action="push")
            done += 1
        except Exception as exc:
            logger.error("推送补偿失败 report=%d: %s", rid, exc)
    if done:
        logger.info("推送补偿 sweep 完成，处理 %d 个实例", done)
    return done
