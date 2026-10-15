"""模板相关 Pydantic v2 schema。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.ai_insight import (
    MAX_SYSTEM_PROMPT_CHARS,
    MAX_SYSTEM_RULES_CHARS,
)
from app.services.period_utils import PERIOD_PATTERN


class InsightAIConfig(BaseModel):
    """「AI 摘要」槽位的写作配置（全部可选，缺省 = 沿用默认行为）。

    - prompt：本段写作要求（想总结什么、以什么口吻）
    - sample_text：参考范文（用户以往写过的真实段落，AI 照它的句式与条目数写，
      只把数值与单位换成真实数据）
    - max_chars：本段字数上限（默认 200；留空且范文比 200 字长时以范文长度为准）
    - temperature：采样温度（范文仿写建议 0.3~0.5）
    - threshold_pct：显著波动阈值（百分数；None 用全局 ai_change_threshold）
    - system_prompt / system_rules：**本段专属**的两段 system 规则文本
      （基础规则 / 有范文或写作要求时才追加的规则），留空 = 用全局配置，
      全局也为空 = 用内置常量。文本里可写 {threshold_pct} / {max_chars} 两个变量。

    注意：``prompt`` / ``sample_text`` 进的是 user message（抗提示词注入），
    而 system_prompt / system_rules 改的是 **system**本身 —— 只应放规则，不放用户内容。
    """

    # extra="forbid"：拼错字段名要报错，而不是静默丢弃（否则前端传了却没落库，
    # 排查起来非常费时）
    model_config = ConfigDict(extra="forbid")

    prompt: str | None = Field(default=None, max_length=4000)
    sample_text: str | None = Field(default=None, max_length=4000)
    max_chars: int | None = Field(default=None, ge=50, le=2000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    threshold_pct: float | None = Field(default=None, ge=0.1, le=100.0)
    system_prompt: str | None = Field(
        default=None, max_length=MAX_SYSTEM_PROMPT_CHARS
    )
    system_rules: str | None = Field(
        default=None, max_length=MAX_SYSTEM_RULES_CHARS
    )

    def to_db(self) -> dict[str, Any] | None:
        """落库形态：去 None、去纯空白文本；全空返回 None（不写库 = 用默认）。"""
        data = self.model_dump(exclude_none=True)
        # 纯空白等于"不覆盖"：不落库，否则 from_mapping 会拿到一个空白段
        # （resolve_system_prompt 会回落，但库里留垃圾值会让"是否自定义"显示错）
        for key in ("prompt", "sample_text", "system_prompt", "system_rules"):
            value = data.get(key)
            if isinstance(value, str) and not value.strip():
                data.pop(key)
        return data or None


class BindingItem(BaseModel):
    placeholder: str = Field(min_length=1, max_length=128)
    binding_type: Literal["metric", "insight"]
    binding_ref_id: int | None = None
    binding_key: str | None = Field(default=None, max_length=64)
    # 仅 insight 有意义：该段摘要的提示词 / 参考范文等
    ai_config: InsightAIConfig | None = None

    @model_validator(mode="after")
    def _check_binding(self) -> "BindingItem":
        if self.binding_type == "metric":
            if self.binding_ref_id is None:
                raise ValueError("metric 绑定必须提供 binding_ref_id")
            self.binding_key = None
            self.ai_config = None
        else:
            if self.binding_ref_id is not None:
                raise ValueError("insight 绑定不能有 binding_ref_id")
            # 槽位缺省 = placeholder 名（同行有唯一约束，天然唯一），
            # 使「一个 AI 占位符 = 一段独立摘要」成立
            if not self.binding_key:
                self.binding_key = self.placeholder[:64]
        return self

    def ai_config_for_db(self) -> dict[str, Any] | None:
        """insight 槽位配置的落库值；metric 恒为 None。"""
        if self.binding_type != "insight" or self.ai_config is None:
            return None
        return self.ai_config.to_db()


class BindingsUpdate(BaseModel):
    """全量替换语义；传空数组 = 清空所有绑定。"""

    bindings: list[BindingItem]


class BindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    placeholder: str
    binding_type: str
    binding_ref_id: int | None
    binding_key: str | None
    # ORM 上由 TemplateBinding.ai_config 属性（列名 ai_config_json）提供
    ai_config: dict[str, Any] | None = None


class PlaceholderInfo(BaseModel):
    """模板变量 + 位置特征（供前端判断图表类型指标能否绑定）。"""

    name: str
    is_alone_in_paragraph: bool


class BindingsResult(BaseModel):
    bindings: list[BindingRead]
    cleared_count: int  # 本次替换清掉的旧绑定数（前端提示用）


class VersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_no: int
    file_name: str
    placeholder_count: int
    source: str
    is_current: bool
    created_at: datetime


class TemplateListItem(BaseModel):
    id: int
    name: str
    file_name: str  # 当前版本文件名
    placeholder_count: int
    bound_count: int
    is_active: bool
    created_at: datetime
    owner_id: int | None = None
    owner_name: str | None = None
    current_version_no: int | None = None
    # 当前用户对该模板的权限：manage / edit / view
    permission: str


class TemplateDetail(BaseModel):
    id: int
    name: str
    is_active: bool
    created_at: datetime
    owner_id: int | None
    owner_name: str | None
    permission: str
    current_version: VersionRead | None
    placeholders: list[PlaceholderInfo]
    bindings: list[BindingRead]
    unbound: list[str]


class PreviewRequest(BaseModel):
    period: str = Field(pattern=PERIOD_PATTERN)


class InsightPreviewRequest(BaseModel):
    """模板绑定页「试跑」：用当前周期真实 facts 立即预览某段 AI 摘要。

    只算 facts + 调 AI，不生成 docx、不落库。period 必填（取数需要），
    其余为页面上拟定的配置（保存前就能看到效果）。
    """

    period: str = Field(pattern=PERIOD_PATTERN)
    # 对应的占位符名（仅用于日志/回显；配置以 ai_config 为准）
    placeholder: str | None = Field(default=None, max_length=128)
    ai_config: InsightAIConfig = Field(default_factory=InsightAIConfig)


class InsightPreviewResult(BaseModel):
    """试跑结果：文本 + 两类告警（凭空编造 / 照抄范文示例数字）+ 是否降级。"""

    text: str
    unverified_numbers: list[str] = Field(default_factory=list)
    sample_leak: list[str] = Field(default_factory=list)
    degraded: bool
    retried: bool = False
    model: str = ""
    # 参与本次生成的指标数（让用户知道 AI 看到了多少数据）
    metric_count: int = 0


class GrantItem(BaseModel):
    user_id: int
    permission: Literal["view", "edit"]


class GrantsUpdate(BaseModel):
    grants: list[GrantItem]


class GrantRead(BaseModel):
    user_id: int
    username: str
    display_name: str
    permission: str


class TransferRequest(BaseModel):
    owner_id: int


class EditorConfig(BaseModel):
    """ONLYOFFICE 编辑器配置（含签名后的 JWT）。"""

    enabled: bool
    docserver_url: str
    # DocEditor 初始化 config（前端原样传给 DocsAPI.DocEditor）
    config: dict[str, Any]


class VersionEditResult(BaseModel):
    version: VersionRead
    # 占位符差异：新版本相对上一版本缺失（已自动清理绑定）的变量
    removed_placeholders: list[str]
    added_placeholders: list[str]
    cleared_bindings: int
