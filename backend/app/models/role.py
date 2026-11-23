"""角色组（共享圈）—— 模型。

设计（2026-11-23，.codebuddy/plans/角色与共享圈设计_20260723.md）：

- **角色 = 共享圈（圈人），不是圈资源**：不给 metric_definitions /
  report_tasks 加 role_id 字段；资源仍归属个人，共享规则是
  「A、B 同属某个角色 -> A 能看到 B 的资源（只读）」。
- 一个人可属于 **0..N 个角色**。
- 「用户权限」（users.role 的 admin/user 两档）与「角色组」是两回事。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Role(Base):
    """角色组（共享圈）。

    注意：项目的 ``TimestampMixin`` 只有 ``created_at``（没有 updated_at），
    这里保持一致。
    """

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: 角色名（全局唯一），如「海油专线组」
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RoleMember(Base):
    """角色成员（一人可属多个角色）。"""

    __tablename__ = "role_members"
    __table_args__ = (
        UniqueConstraint("role_id", "user_id", name="uq_role_member"),
        # 反查「我加入了哪些角色」-> 再查同组成员
        Index("ix_role_members_user", "user_id"),
        Index("ix_role_members_role", "role_id"),
    )

    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    added_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
