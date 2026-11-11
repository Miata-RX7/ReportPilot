"""统一导入所有模型，确保 Alembic autogenerate 能发现全部表。"""

from app.models.ai_call import AiCallLog
from app.models.ai_prompt import AiPromptVersion
from app.models.app_setting import AppSetting
from app.models.base import Base
from app.models.datasource import DataSource
from app.models.metric import MetricDefinition
from app.models.report import (
    PushRecord,
    Report,
    ReportAudit,
    ReportSummary,
    ReportTaskFile,
)
from app.models.task import OwnershipTransfer, ReportTask
from app.models.template import (
    TemplateBinding,
    TemplateGrant,
    TemplateVersion,
    WordTemplate,
)
from app.models.user import User

__all__ = [
    "Base",
    "User",
    "AppSetting",
    "AiCallLog",
    "AiPromptVersion",
    "DataSource",
    "MetricDefinition",
    "WordTemplate",
    "TemplateVersion",
    "TemplateBinding",
    "TemplateGrant",
    "ReportTask",
    "Report",
    "ReportAudit",
    "PushRecord",
]
