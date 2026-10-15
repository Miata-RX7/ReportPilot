"""系统配置覆盖项（页面可编辑的邮件 / AI 配置）。

设计：`.env` 仍是默认值来源，本表只存"页面保存过的覆盖项"，
读取时由 services.runtime_config 合并（DB 覆盖 .env）。
密钥类字段（SMTP 密码 / AI API Key）以 Fernet 加密存储，
接口只返回"是否已配置"，绝不返回明文或密文。
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    # 配置项名（如 smtp_host / ai_model / ai_api_key），白名单见 runtime_config
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    # 非密钥存明文，密钥存 Fernet.encrypt() 的完整 Base64 token
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )