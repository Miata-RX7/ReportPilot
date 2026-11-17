"""指标「同名」与「删除自动解绑」的规则测试（2026-11-17）。

背景：指标按创建人隔离可见，但重名唯一约束曾经是**全局**的 ——
普通用户撞到别人的同名指标只会看到一句「指标名称已存在」，
而那个指标他根本看不到。现在唯一作用域改成 ``(owner_id, name)``：
**同一创建人内不能重名，不同创建人可以同名**。

另外锁住「删除指标自动解除模板绑定」这条 —— 之前出过 500
（``template_bindings`` 只有 version_id、没有 template_id），
就是因为只测了「删除没有绑定的指标」，循环没进去就没触发。
"""

from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.deps import get_current_user
from app.database import get_db
from app.main import app
from app.models import Base
from app.models.template import TemplateBinding, TemplateVersion, WordTemplate
from app.models.user import User

DATASOURCE_PAYLOAD = {
    "name": "生产 Zabbix",
    "type": "zabbix",
    "config": {
        "url": "http://zabbix.example.com",
        "auth_type": "bearer",
        "timeout": 10,
        "verify_ssl": False,
    },
    "secret": "zabbix-token-xyz7890",
}


def _metric_payload(name: str) -> dict:
    return {
        "name": name,
        "data_source_id": 1,
        "metric_type": "scalar",
        "query_config": {
            "host_group_ids": ["4"],
            "host_ids": ["10554"],
            "item_ids": ["42253"],
        },
        "calc_formula": {"agg": "avg", "compare": "mom", "baseline_lookback": 1},
        "unit": "%",
    }


@pytest_asyncio.fixture
async def api() -> AsyncGenerator[tuple[AsyncClient, async_sessionmaker[AsyncSession]], None]:
    """带两个用户的 API：token 用「当前用户」切换来模拟不同 owner。"""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    users = {
        1: User(
            id=1,
            username="alice",
            email="alice@test.local",
            display_name="Alice",
            source="local",
            role="user",
            is_active=True,
        ),
        2: User(
            id=2,
            username="bob",
            email="bob@test.local",
            display_name="Bob",
            source="local",
            role="user",
            is_active=True,
        ),
    }
    # 数据源创建是 admin-only -> 用它建数据源，再切回普通用户测指标
    admin = User(
        id=3,
        username="admin",
        email="admin@test.local",
        display_name="Admin",
        source="local",
        role="admin",
        is_active=True,
    )
    users[3] = admin
    current: dict[str, User] = {"user": admin}

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, session_factory, users, current
    app.dependency_overrides.clear()
    await engine.dispose()


class TestMetricNameScope:
    """重名唯一性：同一创建人内唯一，跨创建人允许。"""

    async def test_same_owner_duplicate_name_rejected(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession], dict, dict]
    ) -> None:
        client, _, users, current = api
        current["user"] = users[3]  # admin 建数据源
        resp = await client.post("/api/datasources", json=DATASOURCE_PAYLOAD)
        assert resp.status_code == 201, resp.text
        current["user"] = users[1]  # 切回 alice

        resp = await client.post("/api/metrics", json=_metric_payload("线路可用率"))
        assert resp.status_code == 201, resp.text

        # 同一个用户再建同名 -> 409
        resp = await client.post("/api/metrics", json=_metric_payload("线路可用率"))
        assert resp.status_code == 409, resp.text
        assert "同名" in resp.text

    async def test_different_owner_same_name_allowed(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession], dict, dict]
    ) -> None:
        client, _, users, current = api
        current["user"] = users[3]  # admin 建数据源
        resp = await client.post("/api/datasources", json=DATASOURCE_PAYLOAD)
        assert resp.status_code == 201, resp.text
        current["user"] = users[1]  # 切回 alice

        resp = await client.post("/api/metrics", json=_metric_payload("线路可用率"))
        assert resp.status_code == 201, resp.text
        alice_id = resp.json()["id"]

        # 换一个人（bob）-> 建同名 -> **应该成功**
        current["user"] = users[2]
        resp = await client.post("/api/metrics", json=_metric_payload("线路可用率"))
        assert resp.status_code == 201, resp.text
        bob_id = resp.json()["id"]

        assert alice_id != bob_id, "两个同名指标应是两条独立记录"

        # 各自的列表里都能看到自己的那个
        resp = await client.get("/api/metrics")
        names = [m["name"] for m in resp.json()]
        assert names.count("线路可用率") == 1, "bob 只能看到自己的同名指标"

    async def test_rename_conflict_only_within_owner(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession], dict, dict]
    ) -> None:
        client, _, users, current = api
        current["user"] = users[3]  # admin 建数据源
        resp = await client.post("/api/datasources", json=DATASOURCE_PAYLOAD)
        assert resp.status_code == 201, resp.text
        current["user"] = users[1]  # 切回 alice
        resp = await client.post("/api/metrics", json=_metric_payload("A"))
        assert resp.status_code == 201, resp.text
        a_id = resp.json()["id"]

        current["user"] = users[2]
        resp = await client.post("/api/metrics", json=_metric_payload("B"))
        assert resp.status_code == 201, resp.text
        b_id = resp.json()["id"]

        # bob 把自己的 B 改名为 A -> 允许（alice 的 A 跟他不是同一个 owner）
        resp = await client.put(f"/api/metrics/{b_id}", json={"name": "A"})
        assert resp.status_code == 200, resp.text

        # alice 把自己的 A 改名为 A（自己）-> 不该报冲突（exclude 自己）
        current["user"] = users[1]
        resp = await client.put(f"/api/metrics/{a_id}", json={"name": "A"})
        assert resp.status_code == 200, resp.text


class TestDeleteMetricUnbindsTemplates:
    """删除指标要连带解除模板绑定（否则报告生成会失败）。"""

    async def test_delete_metric_removes_template_bindings(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession], dict, dict]
    ) -> None:
        client, session_factory, users, current = api
        current["user"] = users[3]  # admin 建数据源
        resp = await client.post("/api/datasources", json=DATASOURCE_PAYLOAD)
        assert resp.status_code == 201, resp.text
        current["user"] = users[1]  # 切回 alice
        resp = await client.post("/api/metrics", json=_metric_payload("待删指标"))
        assert resp.status_code == 201, resp.text
        metric_id = resp.json()["id"]

        # 造一个模板 + 版本 + 绑定（绑到这个指标）
        async with session_factory() as db:
            tpl = WordTemplate(
                name="T", owner_id=1, file_path="/tmp/t.docx", is_active=True
            )
            db.add(tpl)
            await db.flush()
            version = TemplateVersion(
                template_id=tpl.id,
                version_no=1,
                file_path="/tmp/t-v1.docx",
                placeholders_json=["aaa"],
            )
            db.add(version)
            await db.flush()
            binding = TemplateBinding(
                version_id=version.id,
                placeholder="aaa",
                binding_type="metric",
                binding_ref_id=metric_id,
            )
            db.add(binding)
            await db.commit()
            version_id = version.id

        # 删除指标前：能查到它被绑在哪
        resp = await client.get(f"/api/metrics/{metric_id}/template-bindings")
        assert resp.status_code == 200, resp.text
        bindings = resp.json()
        assert len(bindings) == 1, bindings
        assert bindings[0]["placeholder"] == "aaa"

        # 删除指标 -> 204，且绑定被自动解除
        resp = await client.delete(f"/api/metrics/{metric_id}")
        assert resp.status_code == 204, resp.text

        async with session_factory() as db:
            left = (
                await db.scalars(
                    TemplateBinding.__table__.select().where(
                        TemplateBinding.__table__.c.version_id == version_id
                    )
                )
            ).all()
        assert left == [], "删除指标后模板绑定应被自动解除"
