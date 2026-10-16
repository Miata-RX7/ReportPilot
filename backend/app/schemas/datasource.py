"""数据源相关 Pydantic v2 schema。Create / Read 分开定义，不复用。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DataSourceConfig(BaseModel):
    """非敏感连接配置（落库到 config_json）。"""

    url: str = Field(min_length=1, max_length=512)
    auth_type: Literal["bearer"] = "bearer"
    timeout: int = Field(default=30, ge=1, le=300)
    verify_ssl: bool = True


class DataSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: Literal["zabbix", "aiops"]
    config: DataSourceConfig
    secret: str = Field(min_length=1)  # 明文，仅创建时接收，后端加密存储


class DataSourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    config: DataSourceConfig | None = None
    # None 或 "" -> 保留原密文；非空 -> 重新加密覆盖
    secret: str | None = None
    is_active: bool | None = None


class DataSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str
    config: DataSourceConfig = Field(validation_alias="config_json")
    is_active: bool
    created_at: datetime
    secret_hint: str = ""  # 直接映射 DB 列（"****a1b2"），无 secret_encrypted 时为空串

    @field_validator("secret_hint", mode="before")
    @classmethod
    def _none_to_empty(cls, v: object) -> object:
        return v or ""


class ConnectionTestResult(BaseModel):
    success: bool
    message: str


# ---------- 取数范围下拉（三级级联）----------


class HostGroupRef(BaseModel):
    """主机组下拉项（AIOps 语义为「模型」）。

    instance_num / template_num 仅 AIOps 返回，供前端提示「这个模型有多少实例」，
    并说明为什么 0 实例的模型不会出现在列表里。
    """

    group_id: str
    name: str
    instance_num: int | None = None
    template_num: int | None = None


class HostRef(BaseModel):
    host_id: str
    name: str
    groups: list[HostGroupRef] = Field(default_factory=list)
    # AIOps 的 show_attr.ip（实测恒为字符串）；Zabbix 无IP 概念
    ip: str | None = None


class AIOpsColumn(BaseModel):
    """list 型监控项的一列。

    value_type 决定该列能配成什么指标（见 services/aiops_keys.py 的判定表）：
    - text列 -> 只能text
    - number + has_history -> scalar / curve
    - number + 无 history -> 只能 scalar（当前值兜单点，画曲线必空）
    """

    column_id: int
    name: str
    unit: str
    value_type: Literal["number", "text"]
    has_history: bool
    is_primary: bool


class ItemRowRef(BaseModel):
    """list 型监控项的一个主键行。

    只返回**该列当前值非空**的行 —— 实测「当前值非空」⟺「有历史」，
    过滤掉空行可以避免用户选到「选了没数据」的项。
    """

    row_key: str
    current_value: str = ""
    as_of: int = 0


class ItemRef(BaseModel):
    """监控项下拉项。id 一律以字符串返回，与 query_config 的字符串口径一致。

    ``aiops_*`` 字段仅 AIOps 有值，Zabbix 一律为 None/空 —— 前端据此切换级联层级。
    """

    item_id: str
    name: str
    key: str
    host_id: str
    host: str
    value_type: int
    units: str
    # ---- AIOps 扩展 ----
    # 一律用 None 而非空列表 / 空串：配合端点的 response_model_exclude_none=True，
    # **Zabbix 的响应体逐字不变**，不给存量前端凭空塞几个空字段。
    aiops_data_type: Literal["number", "text", "list"] | None = None
    aiops_columns: list[AIOpsColumn] | None = None
    aiops_template_id: int | None = None
    aiops_template_name: str | None = None
    aiops_cycle: int | None = None
