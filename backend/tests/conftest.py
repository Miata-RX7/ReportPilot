"""测试全局配置：必须在导入 app 任何模块之前设置环境变量。"""

import os
from collections.abc import Iterator

from cryptography.fernet import Fernet

os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
# 隔离部署期 .env：测试不应继承真实 ONLYOFFICE 配置（env 变量优先于 .env）
os.environ.setdefault("ONLYOFFICE_URL", "")
os.environ.setdefault("ONLYOFFICE_JWT_SECRET", "")
os.environ.setdefault("PUBLIC_BASE_URL", "")
os.environ.setdefault("ONLYOFFICE_INTERNAL_URL", "")

import pytest  # noqa: E402

from app.models.user import User  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_runtime_config() -> Iterator[None]:
    """每个测试前后清空运行时配置覆盖缓存，避免用例之间相互串味。"""
    from app.services import runtime_config  # noqa: PLC0415

    runtime_config.reset_cache()
    yield
    runtime_config.reset_cache()


@pytest.fixture(autouse=True)
def _reset_adapter_list_cache() -> Iterator[None]:
    """每个测试前后清空 Adapter 浏览类缓存。

    浏览缓存是**进程内**的，测试之间不清理就会串味：上一个用例 respx 打的
    响应被下一个用例命中，于是断言拿到上游根本不相关的旧数据
    （实测test_items_q_filter 就是这样红的）。
    """
    from app.services.adapters.base import clear_list_cache  # noqa: PLC0415

    clear_list_cache()
    yield
    clear_list_cache()


@pytest.fixture
def fake_user() -> User:
    """API 测试用的固定 admin 用户（dependency_overrides 注入，不触库）。"""
    return User(
        id=1,
        username="tester",
        email="tester@test.local",
        display_name="Tester",
        source="local",
        role="admin",
        is_active=True,
    )
