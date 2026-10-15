"""数据库连接：SQLAlchemy 2.0 async engine 与会话工厂。"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import get_settings

settings = get_settings()

# 必须用 NullPool：调度器在 APScheduler 线程里 `asyncio.run()`，每次都新建并**关闭**
# 一个事件循环。若用默认连接池，线程里创建的连接会被放回全局池，随后被主循环的请求
# 捞去使用 -> asyncpg 报 "Event loop is closed" / "attached to a different loop"，
# 表现为随机接口 500（最常见是 /api/auth/me，前端进每个页面都要调它）。
# NullPool 下连接随会话在**同一个 loop 内**创建与关闭，天然不会跨 loop 复用。
# 代价是每次请求新建连接（少量握手开销），对本系统规模可忽略。
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    poolclass=NullPool,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：按请求提供一个异步会话。"""
    async with AsyncSessionLocal() as session:
        yield session
