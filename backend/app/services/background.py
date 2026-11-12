"""后台任务：不阻塞 HTTP 请求（2026-11-12）。

背景：手动触发报告时，``scheduler.run_task_instance`` 里直接
``await report_service.run_generation(...)`` —— **请求会一直等到整份报告生成完**
（渲染 DOCX + 取全部指标 + AI 洞察 + 转 PDF），几十秒到几分钟。
前端默认超时 30 秒 -> 「请求超时」。

改成后台跑：接口立即返回 report_id，生成在后台继续。
前端本来就假设「立即返回」（提示「已触发，正在生成报告（实例 #id）」）。

注意：业务失败必须由协程**自己**处理（``run_generation`` 内部已经会把实例置
failed 并记 last_error），这里只兜底「协程本身抛异常」的情况。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger(__name__)

#: 持有后台任务引用，防止被 GC 提前回收（asyncio 只持有弱引用）
_TASKS: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task[Any]:
    """把协程丢到后台执行。异常只记日志（不冒泡到请求）。"""
    task = asyncio.create_task(coro, name=name)
    _TASKS.add(task)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: asyncio.Task[Any]) -> None:
    _TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("后台任务异常 name=%s", task.get_name(), exc_info=exc)
