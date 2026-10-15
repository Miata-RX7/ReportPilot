"""AI 系统提示词的版本历史（全局两段文本的审计与回滚）。

回答「这两段规则是谁、在什么时候改成了什么」：每次保存 / 恢复默认 / 回滚都追加一行，
**append-only** —— 回滚是「读取历史文本 → 写一条新的当前版本」，绝不改写已有行，
所以历史本身就是可信的账本。

与相邻表的分工：
- `app_settings` 存**当前生效值**（key/value，两段文本各一个 key）
- `AiPromptVersion` 存**历史**，含操作人快照（`actor_name`，用户改名后历史不失真）
- `ai_call_logs` 记「实际发出去的 messages」——从那边能看到某次调用用了哪一版

只存规则文本，不含任何密钥（这两段本来就不是密钥，可回显）。
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# 变更动作：save 保存 / reset_default 恢复内置默认 / rollback 回滚到某个历史版本
AI_PROMPT_ACTIONS = ("save", "reset_default", "rollback")


class AiPromptVersion(Base):
    __tablename__ = "ai_prompt_versions"
    __table_args__ = (
        # 主查询方向：按时间倒序翻历史
        Index("ix_ai_prompt_version_recent", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # 操作人姓名快照：users.username 改名后，历史里仍显示当时的操作人
    actor_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    # save | reset_default | rollback
    action: Mapped[str] = mapped_column(String(16), default="save", nullable=False)
    # 变更后生效的两段文本；恢复默认时两者都为空串（= 回落内置常量）
    base_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    system_rules: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 仅 rollback：回滚所依据的那条历史版本 id（便于顺着链路往回追）
    replaced_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_prompt_versions.id"), nullable=True
    )
    # 变更前生效的文本（reset_default 时即"清空"前的内容），便于对比
    previous_base_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_system_rules: Mapped[str | None] = mapped_column(Text, nullable=True)

    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )