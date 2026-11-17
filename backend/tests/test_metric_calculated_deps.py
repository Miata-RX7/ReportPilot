"""计算型指标的依赖关系（2026-11-17）。

三条规则：

1. 计算型指标**只能引用自己创建的指标** —— 否则删除时会出现隐蔽的越权
   （A 建了引用 B 指标的算式 X，B 删自己的指标 -> 级联删除会删掉 A 的 X）。
2. 删除被引用的指标会**连带删除**引用它的计算型指标（算式失效了就沒意义了）。
3. 计算型指标被删除时，它在模板里的绑定也一并解除（否则模板留悬空绑定 ->
   报告生成报「绑定的指标不存在或已停用」）。
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


def _scalar_payload(name: str) -> dict:
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


def _calc_payload(name: str, dep_metric_id: int) -> dict:
    return {
        "name": name,
        "data_source_id": 1,
        "metric_type": "calculated",
        "query_config": {
            "formula": "(100-a)/100*minutes",
            "variables": [{"name": "a", "metric_id": dep_metric_id}],
            "builtin_minutes": True,
        },
        "calc_formula": {"agg": "avg", "compare": "none", "baseline_lookback": None},
    }


@pytest_asyncio.fixture
async def api() -> AsyncGenerator[tuple[AsyncClient, async_sessionmaker[AsyncSession], dict, dict], None]:
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
            id=1, username="alice", email="alice@test.local",
            display_name="Alice", source="local", role="user", is_active=True,
        ),
        2: User(
            id=2, username="bob", email="bob@test.local",
            display_name="Bob", source="local", role="user", is_active=True,
        ),
        3: User(
            id=3, username="admin", email="admin@test.local",
            display_name="Admin", source="local", role="admin", is_active=True,
        ),
    }
    current: dict[str, User] = {"user": users[3]}
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, session_factory, users, current
    app.dependency_overrides.clear()
    await engine.dispose()


async def _make_ds(client: AsyncClient, users: dict, current: dict) -> None:
    current["user"] = users[3]
    resp = await client.post("/api/datasources", json=DATASOURCE_PAYLOAD)
    assert resp.status_code == 201, resp.text


class TestCalculatedDependency:
    async def test_cannot_reference_other_owner_metric(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession], dict, dict]
    ) -> None:
        """跨 owner 引用必须被拒 —— 否则级联删除会越权删别人的东西。"""
        client, _, users, current = api
        await _make_ds(client, users, current)

        current["user"] = users[1]  # alice 建基础指标
        resp = await client.post("/api/metrics", json=_scalar_payload("基础指标"))
        assert resp.status_code == 201, resp.text
        dep_id = resp.json()["id"]

        current["user"] = users[2]  # bob 想引用 alice 的指标
        resp = await client.post("/api/metrics", json=_calc_payload("bob的算式", dep_id))
        assert resp.status_code == 422, resp.text
        assert "不是您创建的指标" in resp.text

    async def test_delete_base_metric_cascades_to_calculated(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession], dict, dict]
    ) -> None:
        """删除被引用的指标 -> 引用它的计算型指标一并删除。"""
        client, session_factory, users, current = api
        await _make_ds(client, users, current)

        current["user"] = users[1]
        resp = await client.post("/api/metrics", json=_scalar_payload("基础指标"))
        assert resp.status_code == 201, resp.text
        dep_id = resp.json()["id"]
        resp = await client.post("/api/metrics", json=_calc_payload("我的算式", dep_id))
        assert resp.status_code == 201, resp.text
        calc_id = resp.json()["id"]

        # 引用关系可见
        resp = await client.get(f"/api/metrics/{dep_id}/referenced-by")
        assert resp.status_code == 200, resp.text
        refs = resp.json()
        assert len(refs) == 1, refs
        assert refs[0]["calc_id"] == calc_id
        assert refs[0]["var_name"] == "a"

        # 删除基础指标 -> 级联删除计算型指标
        resp = await client.delete(f"/api/metrics/{dep_id}")
        assert resp.status_code == 204, resp.text

        async with session_factory() as db:
            from app.models.metric import MetricDefinition

            calc = await db.get(MetricDefinition, calc_id)
            assert calc is not None
            assert calc.is_active is False, "计算型指标应被级联删除（软删除）"

    async def test_cascade_also_unbinds_templates(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession], dict, dict]
    ) -> None:
        """级联删除计算型指标时，它在模板里的绑定也要解除（否则模板留悬空绑定）。"""
        client, session_factory, users, current = api
        await _make_ds(client, users, current)

        current["user"] = users[1]
        resp = await client.post("/api/metrics", json=_scalar_payload("基础指标"))
        dep_id = resp.json()["id"]
        resp = await client.post("/api/metrics", json=_calc_payload("我的算式", dep_id))
        calc_id = resp.json()["id"]

        # 模板 + 版本 + 绑定（绑到计算型指标）
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
            db.add(
                TemplateBinding(
                    version_id=version.id,
                    placeholder="aaa",
                    binding_type="metric",
                    binding_ref_id=calc_id,
                )
            )
            await db.commit()
            version_id = version.id

        resp = await client.delete(f"/api/metrics/{dep_id}")
        assert resp.status_code == 204, resp.text

        async with session_factory() as db:
            left = (
                await db.scalars(
                    TemplateBinding.__table__.select().where(
                        TemplateBinding.__table__.c.version_id == version_id
                    )
                )
            ).all()
        assert left == [], "级联删除计算型指标后，其模板绑定应被解除"

    async def test_broken_deps_flag_on_list(
        self, api: tuple[AsyncClient, async_sessionmaker[AsyncSession], dict, dict]
    ) -> None:
        """依赖失效时，列表里要能看出 broken_deps（前端据此打 ⚠ 标记）。

        这里绕过 API 直接在库里停用依赖 —— 模拟「存量隐患」或「外部改动」
        （正常路径下级联删除会把算式一起删掉，不留失效依赖）。
        """
        client, session_factory, users, current = api
        await _make_ds(client, users, current)

        current["user"] = users[1]
        resp = await client.post("/api/metrics", json=_scalar_payload("基础指标"))
        dep_id = resp.json()["id"]
        resp = await client.post("/api/metrics", json=_calc_payload("我的算式", dep_id))
        calc_id = resp.json()["id"]

        resp = await client.get("/api/metrics")
        items = {m["id"]: m for m in resp.json()}
        assert items[calc_id]["broken_deps"] == [], "依赖都还在时不该有标记"

        # 直接停用依赖（绕过 API）
        from app.models.metric import MetricDefinition

        async with session_factory() as db:
            dep = await db.get(MetricDefinition, dep_id)
            assert dep is not None
            dep.is_active = False
            await db.commit()

        resp = await client.get("/api/metrics")
        items = {m["id"]: m for m in resp.json()}
        broken = items[calc_id]["broken_deps"]
        assert len(broken) == 1, broken
        assert broken[0]["metric_id"] == dep_id
        assert broken[0]["name"] == "基础指标"
        assert broken[0]["var_name"] == "a"
