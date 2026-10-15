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
    """主机组下拉项。"""

    group_id: str
    name: str


class HostRef(BaseModel):
    host_id: str
    name: str
    groups: list[HostGroupRef] = Field(default_factory=list)


class ItemRef(BaseModel):
    """监控项下拉项。id 一律以字符串返回，与 query_config 的字符串口径一致。"""

    item_id: str
    name: str
    key: str
    host_id: str
    host: str
    value_type: int
    units: str
