"""应用配置：全部通过环境变量注入，不写死任何环境相关值。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 应用
    app_name: str = "ReportPilot"
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:5173"]

    # 数据库
    database_url: str = (
        "postgresql+asyncpg://reportpilot:reportpilot@localhost:5432/reportpilot"
    )

    # 认证（JWT_SECRET 必填，缺失启动 fail-fast，与 ENCRYPTION_KEY 同等对待）
    jwt_secret: str = ""
    jwt_expire_days: int = 7

    # 敏感信息加密（Fernet 密钥，必填，缺失时启动报错）
    encryption_key: str = ""

    # Zabbix 默认参数（可被数据源 config 覆盖）
    zabbix_default_timeout: int = 30
    zabbix_max_retries: int = 2

    # 文件存储（Word 模板与生成的报告）
    storage_dir: str = "./storage"

    # AI（OpenAI 兼容协议，可指向任意兼容服务）
    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = ""
    ai_model: str = "gpt-4o-mini"
    ai_timeout_seconds: int = 60
    # 显著波动判定阈值（|change_pct| 比例），注入 AI prompt
    ai_change_threshold: float = 0.1
    # AI 系统提示词（两段，管理员可在「系统设置」页编辑，改完立即生效）。
    # 空串 = 用 app/services/ai_insight.py 里的内置常量；文本里可写
    # {threshold_pct} / {max_chars} 两个动态变量，其余花括号原样保留。
    ai_system_prompt: str = ""
    ai_system_rules: str = ""

    # SMTP（审核推送）
    smtp_host: str = ""
    smtp_port: int = 25
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = False

    # ONLYOFFICE Document Server（在线编辑模板文档）
    # onlyoffice_url: 浏览器可达的 DS 地址（如 http://localhost:8081）
    # public_base_url: DS 回拉后端文件/回调的后端地址（容器内如 http://reportpilot-backend:8000）
    onlyoffice_url: str = ""
    # onlyoffice_internal_url: **后端容器**可达的 DS 地址（如 http://onlyoffice）。
    # DS 回调里的文件下载 URL 由 DS 按自己的视角生成（常是 localhost / 容器本地名），
    # 后端拿那个 host 连不通，必须改写成内网可达地址。不配置则原样使用 DS 的 URL。
    onlyoffice_internal_url: str = ""
    onlyoffice_jwt_secret: str = ""
    public_base_url: str = ""
    editor_token_ttl_seconds: int = 600  # 文件/回调一次性 token 有效期

    # ---- 报告任务 / 确认流程 ----
    # APP_BASE_URL：**浏览器与邮件可达**的对外地址，用于拼确认链接。
    # 不能复用 public_base_url（那是 ONLYOFFICE 用的容器内网地址）。
    app_base_url: str = "http://localhost:8080"
    # cron 求值与"下次执行"展示时区。容器是 UTC，不设会导致时刻错位 8 小时
    report_timezone: str = "Asia/Hong_Kong"
    # 确认链接有效期（小时）
    confirm_token_ttl_hours: int = 72
    # 未确认提醒间隔（小时）
    reminder_interval_hours: int = 24
    # 调度器开关。多副本部署时只在一个副本开启（见 README）
    scheduler_enabled: bool = True
    # 错过 cron 的宽限期（小时）：期间内补跑，超过则跳过并记 last_skipped_at
    misfire_grace_hours: int = 1
    # 确认链接过期后，邮件里提示联系谁重新发送
    task_contact_email: str = ""

    @property
    def onlyoffice_enabled(self) -> bool:
        """三项配置齐全才启用在线编辑，否则前端降级为只读预览。"""
        return bool(
            self.onlyoffice_url and self.onlyoffice_jwt_secret and self.public_base_url
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
