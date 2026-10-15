"""用户模型。source 字段区分 local / ad，AD 同步为 Phase 2 预留。"""

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    # local=本地账号；ad=AD 同步（Phase 2，仅预留）
    source: Mapped[str] = mapped_column(String(16), default="local", nullable=False)
    # AD 账号无本地密码，故允许为空
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # admin / user（Phase 1 两档角色）
    role: Mapped[str] = mapped_column(
        String(16), default="user", server_default="user", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
