"""ReportPilot 后端入口。"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    ai_calls,
    auth,
    confirmations,
    datasources,
    metrics,
    push_records,
    reports,
    system,
    tasks,
    templates,
    users,
)
from app.config import get_settings
from app.services.crypto import get_fernet

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("reportpilot")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # 启动即校验加密密钥与 JWT 密钥，缺失/非法直接失败，不静默降级
    get_fernet()
    if not get_settings().jwt_secret:
        raise RuntimeError("JWT_SECRET 未配置，请设置环境变量后重启")
    logger.info("加密密钥与 JWT 密钥校验通过")

    # 页面保存的邮件 / AI 配置（DB）覆盖 .env：启动即载入进程内缓存，
    # 使发信与 AI 摘要无需每次查询 DB。失败时沿用 .env，不阻断启动。
    from app.database import AsyncSessionLocal
    from app.services import runtime_config

    try:
        async with AsyncSessionLocal() as session:
            await runtime_config.refresh(session)
    except Exception as exc:
        logger.warning("运行时配置载入失败（本次沿用 .env 默认值）: %s", exc)

    # 调度器（报告任务）：DB 是唯一真相源，这里从 DB 装载启用中的任务
    from app.services import scheduler

    try:
        await scheduler.start_scheduler()
    except Exception as exc:  # 调度器异常不应阻断 API 服务
        logger.error("调度器启动失败（API 仍可用）: %s", exc, exc_info=True)
    try:
        yield
    finally:
        try:
            await scheduler.stop_scheduler()
        except Exception as exc:
            logger.warning("调度器关闭异常: %s", exc)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(
        datasources.router, prefix="/api/datasources", tags=["datasources"]
    )
    app.include_router(metrics.router, prefix="/api/metrics", tags=["metrics"])
    app.include_router(
        templates.router, prefix="/api/templates", tags=["templates"]
    )
    app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
    app.include_router(tasks.router, prefix="/api/report-tasks", tags=["report-tasks"])
    # 公开确认接口（凭邮件里的一次性 token，无需登录）
    app.include_router(
        confirmations.router, prefix="/api/confirm", tags=["confirm"]
    )
    app.include_router(
        push_records.router, prefix="/api/push-records", tags=["push-records"]
    )
    # AI 调用留痕（只读）：回答「每次请求 AI 的参数与 prompt 是什么」
    app.include_router(
        ai_calls.router, prefix="/api/ai-calls", tags=["ai-calls"]
    )
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(users.router, prefix="/api/users", tags=["users"])
    app.include_router(system.router, prefix="/api/system", tags=["system"])

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    logger.info("ReportPilot backend initialized")
    return app


app = create_app()
