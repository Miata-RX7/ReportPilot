"""系统提示词（两段规则文本）接口测试。

覆盖：管理员鉴权、全局读/写、恢复默认、版本历史（append-only）、回滚、
长度上限、内置默认实时下发，以及绑定级覆盖字段是否被 schema 正确接受。

红线提醒：这两段文本**不是密钥**，可回显；本文件所有断言都不应出现密钥。
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_current_user
from app.database import get_db
from app.main import app
from app.models.ai_prompt import AiPromptVersion
from app.models.app_setting import AppSetting
from app.models.base import Base
from app.models.user import User
from app.schemas.template import InsightAIConfig
from app.services import ai_insight

SessionFactory = async_sessionmaker[AsyncSession]


async def _make_api(
    user: User | None,
) -> AsyncGenerator[tuple[AsyncClient, SessionFactory], None]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, factory
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest_asyncio.fixture
async def admin_api(
    fake_user: User,
) -> AsyncGenerator[tuple[AsyncClient, SessionFactory], None]:
    async for pair in _make_api(fake_user):
        yield pair


@pytest_asyncio.fixture
async def viewer_api() -> AsyncGenerator[tuple[AsyncClient, SessionFactory], None]:
    viewer = User(
        id=2,
        username="viewer",
        email="viewer@test.local",
        display_name="Viewer",
        source="local",
        role="user",
        is_active=True,
    )
    async for pair in _make_api(viewer):
        yield pair


@pytest_asyncio.fixture
async def anon_api() -> AsyncGenerator[tuple[AsyncClient, SessionFactory], None]:
    async for pair in _make_api(None):
        yield pair


# --------------------------------------------------------------------------
# 鉴权
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_requires_login(anon_api: tuple[AsyncClient, SessionFactory]) -> None:
    client, _ = anon_api
    assert (await client.get("/api/system/settings/ai-prompt")).status_code == 401


@pytest.mark.asyncio
async def test_write_requires_admin(
    viewer_api: tuple[AsyncClient, SessionFactory],
) -> None:
    client, _ = viewer_api
    r = await client.put(
        "/api/system/settings/ai-prompt", json={"base_prompt": "x", "system_rules": ""}
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_reset_and_rollback_require_admin(
    viewer_api: tuple[AsyncClient, SessionFactory],
) -> None:
    client, _ = viewer_api
    assert (await client.post("/api/system/settings/ai-prompt/reset")).status_code == 403
    r = await client.post("/api/system/settings/ai-prompt/rollback", json={"version_id": 1})
    assert r.status_code == 403

# --------------------------------------------------------------------------
# 读取 / 保存
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defaults_before_any_change(
    admin_api: tuple[AsyncClient, SessionFactory],
) -> None:
    client, _ = admin_api
    r = await client.get("/api/system/settings/ai-prompt")
    assert r.status_code == 200
    body = r.json()
    assert body["base_prompt"] == ""
    assert body["system_rules"] == ""
    assert body["has_custom"] is False
    # 内置默认由后端实时下发（前端不硬编码，规则文案会随代码演进）
    assert body["default_base_prompt"] == ai_insight.SYSTEM_PROMPT
    assert body["default_system_rules"] == ai_insight.SYSTEM_PROMPT_USER_INPUT


@pytest.mark.asyncio
async def test_save_then_read_roundtrip(
    admin_api: tuple[AsyncClient, SessionFactory],
) -> None:
    client, factory = admin_api
    r = await client.put(
        "/api/system/settings/ai-prompt",
        json={"base_prompt": "只允许说 CPU", "system_rules": "  ", "note": "第一次改"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["base_prompt"] == "只允许说 CPU"
    assert body["system_rules"] == "", "纯空白应归一为空（= 回落内置默认）"
    assert body["has_custom"] is True

    async with factory() as session:
        rows = {
            row.key: row.value
            for row in (await session.execute(select(AppSetting))).scalars().all()
        }
    assert rows["ai_system_prompt"] == "只允许说 CPU"
    assert rows["ai_system_rules"] == ""

    again = (await client.get("/api/system/settings/ai-prompt")).json()
    assert again["base_prompt"] == "只允许说 CPU"


@pytest.mark.asyncio
async def test_settings_overview_exposes_custom_flag(
    admin_api: tuple[AsyncClient, SessionFactory],
) -> None:
    """概览里要能一眼看出"已自定义"，设置页卡片状态标签靠它。"""
    client, _ = admin_api
    assert (await client.get("/api/system/settings")).json()["ai_prompt_custom"] is False
    await client.put(
        "/api/system/settings/ai-prompt",
        json={"base_prompt": "自定义规则", "system_rules": ""},
    )
    assert (await client.get("/api/system/settings")).json()["ai_prompt_custom"] is True


@pytest.mark.asyncio
async def test_save_rejects_too_long_text(
    admin_api: tuple[AsyncClient, SessionFactory],
) -> None:
    client, _ = admin_api
    r = await client.put(
        "/api/system/settings/ai-prompt",
        json={"base_prompt": "x" * 5000, "system_rules": ""},
    )
    assert r.status_code == 422


# --------------------------------------------------------------------------
# 版本历史（append-only）
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_records_every_change(
    admin_api: tuple[AsyncClient, SessionFactory],
) -> None:
    client, _ = admin_api
    await client.put(
        "/api/system/settings/ai-prompt", json={"base_prompt": "v1", "system_rules": ""}
    )
    await client.put(
        "/api/system/settings/ai-prompt",
        json={"base_prompt": "v2", "system_rules": "r2"},
    )
    await client.post("/api/system/settings/ai-prompt/reset")

    rows = (await client.get("/api/system/settings/ai-prompt/versions")).json()
    assert [r["action"] for r in rows] == ["reset_default", "save", "save"]
    assert rows[0]["base_len"] == 0
    assert rows[2]["base_preview"].startswith("v1")
    assert rows[0]["actor_name"] == "tester", "操作人快照要能查到是谁改的"


@pytest.mark.asyncio
async def test_history_detail_contains_full_text(
    admin_api: tuple[AsyncClient, SessionFactory],
) -> None:
    client, _ = admin_api
    await client.put(
        "/api/system/settings/ai-prompt",
        json={"base_prompt": "很长的一段规则" * 30, "system_rules": ""},
    )
    vid = (await client.get("/api/system/settings/ai-prompt/versions")).json()[0]["id"]
    detail = (await client.get(f"/api/system/settings/ai-prompt/versions/{vid}")).json()
    assert detail["base_prompt"] == "很长的一段规则" * 30
    assert detail["system_rules"] == ""


@pytest.mark.asyncio
async def test_history_missing_version_404(
    admin_api: tuple[AsyncClient, SessionFactory],
) -> None:
    client, _ = admin_api
    assert (await client.get("/api/system/settings/ai-prompt/versions/999")).status_code == 404

@pytest.mark.asyncio
async def test_rollback_restores_text_and_appends_history(
    admin_api: tuple[AsyncClient, SessionFactory],
) -> None:
    client, factory = admin_api
    await client.put(
        "/api/system/settings/ai-prompt",
        json={"base_prompt": "要保留的", "system_rules": ""},
    )
    await client.put(
        "/api/system/settings/ai-prompt",
        json={"base_prompt": "后来改坏的", "system_rules": ""},
    )

    versions = (await client.get("/api/system/settings/ai-prompt/versions")).json()
    target = versions[1]["id"]

    r = await client.post(
        "/api/system/settings/ai-prompt/rollback", json={"version_id": target}
    )
    assert r.status_code == 200
    assert r.json()["base_prompt"] == "要保留的"

    rows = (await client.get("/api/system/settings/ai-prompt/versions")).json()
    assert rows[0]["action"] == "rollback"
    assert rows[0]["replaced_from_id"] == target
    assert len(rows) == 3, "append-only：回滚只追加，不删改已有行"
    async with factory() as session:
        stored = (
            (
                await session.execute(
                    select(AiPromptVersion).order_by(AiPromptVersion.id)
                )
            )
            .scalars()
            .all()
        )
    assert [v.base_prompt for v in stored] == ["要保留的", "后来改坏的", "要保留的"]


@pytest.mark.asyncio
async def test_rollback_unknown_version_404(
    admin_api: tuple[AsyncClient, SessionFactory],
) -> None:
    client, _ = admin_api
    r = await client.post(
        "/api/system/settings/ai-prompt/rollback", json={"version_id": 4242}
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_reset_returns_to_builtin_and_can_roll_back(
    admin_api: tuple[AsyncClient, SessionFactory],
) -> None:
    client, factory = admin_api
    await client.put(
        "/api/system/settings/ai-prompt",
        json={"base_prompt": "自定义", "system_rules": "r"},
    )
    body = (await client.post("/api/system/settings/ai-prompt/reset")).json()
    assert body["base_prompt"] == "" and body["system_rules"] == ""
    assert body["has_custom"] is False

    # 关键：真删行，而不是写空串（否则这一行仍是"覆盖项"，判断会含糊）
    async with factory() as session:
        keys = {
            row.key
            for row in (await session.execute(select(AppSetting))).scalars().all()
        }
    assert "ai_system_prompt" not in keys
    assert "ai_system_rules" not in keys

    rows = (await client.get("/api/system/settings/ai-prompt/versions")).json()
    assert rows[0]["action"] == "reset_default"
    back = await client.post(
        "/api/system/settings/ai-prompt/rollback", json={"version_id": rows[1]["id"]}
    )
    assert back.json()["base_prompt"] == "自定义"


# --------------------------------------------------------------------------
# 绑定级覆盖（ai_config_json）与渲染层衔接
# --------------------------------------------------------------------------


def test_binding_config_accepts_system_texts() -> None:
    cfg = InsightAIConfig(system_prompt="本段基础", system_rules="本段追加")
    assert cfg.to_db() == {"system_prompt": "本段基础", "system_rules": "本段追加"}


def test_binding_config_blank_texts_are_dropped() -> None:
    """纯空白 = 不覆盖；两段都空时整体返回 None（与升级前一致）。"""
    assert InsightAIConfig(prompt="  ", system_prompt="\n\t").to_db() is None


def test_binding_config_forbids_unknown_keys() -> None:
    """拼错字段名要报错，而不是静默丢弃（"传了却没落库"极难排查）。"""
    with pytest.raises(ValueError):
        InsightAIConfig(system_propmt="typo")


def test_binding_level_overrides_global() -> None:
    """绑定级优先于全局；只覆盖一段时另一段仍走全局。"""
    from app.config import get_settings

    settings = get_settings().model_copy(
        update={"ai_system_prompt": "全局基础", "ai_system_rules": "全局追加"}
    )
    cfg = ai_insight.InsightConfig.from_mapping({"system_prompt": "本段基础"})
    base, extra = ai_insight.resolve_system_prompt(cfg, settings)
    assert (base, extra) == ("本段基础", "全局追加")


def test_blank_binding_text_falls_back_to_global() -> None:
    from app.config import get_settings

    settings = get_settings().model_copy(update={"ai_system_prompt": "全局基础"})
    cfg = ai_insight.InsightConfig.from_mapping({"system_prompt": "   "})
    base, _ = ai_insight.resolve_system_prompt(cfg, settings)
    assert base == "全局基础"