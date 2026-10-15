"""Declarative Base、公共 Mixin 与跨数据库 JSON 类型。"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# PG 下用 JSONB，其他数据库（如单测 SQLite）退化为普通 JSON
JSONVariant = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
