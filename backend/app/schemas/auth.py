"""认证相关 Pydantic v2 schema。"""

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    display_name: str
    role: str
    source: str
    is_active: bool = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead
