"""template_service 单元测试：版本创建（复制绑定+清理）、回滚、权限判定。"""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.models.template import (
    TemplateBinding,
    TemplateGrant,
    TemplateVersion,
    WordTemplate,
)
from app.models.user import User
from app.services import template_service


@pytest.fixture
async def session(tmp_path: Path):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _base(session: AsyncSession) -> tuple[WordTemplate, TemplateVersion]:
    owner = User(
        id=1,
        username="owner",
        email="o@x.com",
        display_name="Owner",
        source="local",
        role="user",
        is_active=True,
    )
    other = User(
        id=2,
        username="other",
        email="o2@x.com",
        display_name="Other",
        source="local",
        role="user",
        is_active=True,
    )
    session.add_all([owner, other])
    tpl = WordTemplate(name="月报模板", owner_id=1)
    session.add(tpl)
    await session.flush()
    v1 = TemplateVersion(
        template_id=tpl.id,
        version_no=1,
        file_path="tpl_v1.docx",
        placeholders_json=["a", "b"],
        source="upload",
    )
    session.add(v1)
    await session.flush()
    tpl.current_version_id = v1.id
    tpl.file_path = v1.file_path
    tpl.placeholders_json = ["a", "b"]
    session.add_all(
        [
            TemplateBinding(
                version_id=v1.id,
                placeholder="a",
                binding_type="metric",
                binding_ref_id=99,
            ),
            TemplateBinding(
                version_id=v1.id,
                placeholder="b",
                binding_type="insight",
                binding_key="summary",
            ),
        ]
    )
    await session.commit()
    return tpl, v1


class TestCreateVersion:
    async def test_clone_bindings_and_clean_removed(
        self, session: AsyncSession
    ) -> None:
        tpl, v1 = await _base(session)
        v2, removed, added, cleared = await template_service.create_version(
            session,
            tpl,
            file_path="tpl_v2.docx",
            placeholders=["a", "c"],
            actor_id=1,
            source="edit",
        )
        await session.commit()

        assert v2.version_no == 2
        assert tpl.current_version_id == v2.id
        assert tpl.file_path == "tpl_v2.docx"
        assert removed == ["b"]
        assert added == ["c"]
        assert cleared == 1  # b 被清理

        cloned = await template_service.bindings_for_version(session, v2.id)
        assert [b.placeholder for b in cloned] == ["a"]
        assert cloned[0].binding_ref_id == 99
        # 旧版本绑定保留
        old = await template_service.bindings_for_version(session, v1.id)
        assert sorted(b.placeholder for b in old) == ["a", "b"]


class TestSetCurrentVersion:
    async def test_rollback(self, session: AsyncSession) -> None:
        tpl, v1 = await _base(session)
        v2, *_ = await template_service.create_version(
            session,
            tpl,
            file_path="tpl_v2.docx",
            placeholders=["a"],
            actor_id=1,
            source="edit",
        )
        await session.commit()
        await template_service.set_current_version(session, tpl, v1)
        await session.commit()
        assert tpl.current_version_id == v1.id
        assert tpl.file_path == "tpl_v1.docx"
        assert tpl.placeholders_json == ["a", "b"]
        assert v2.id != tpl.current_version_id


class TestPermissions:
    async def test_has_permission(self) -> None:
        assert template_service.has_permission("manage", "edit")
        assert template_service.has_permission("edit", "view")
        assert not template_service.has_permission("view", "edit")
        assert not template_service.has_permission(None, "view")

    async def test_resolve_permission(self, session: AsyncSession) -> None:
        tpl, _ = await _base(session)
        owner = await session.get(User, 1)
        other = await session.get(User, 2)
        admin = User(
            id=9,
            username="admin",
            email="a@x.com",
            display_name="Admin",
            source="local",
            role="admin",
            is_active=True,
        )
        # 属主 -> manage
        assert await template_service.resolve_permission(session, tpl, owner) == "manage"
        # admin -> manage
        assert await template_service.resolve_permission(session, tpl, admin) == "manage"
        # 无授权 -> None
        assert await template_service.resolve_permission(session, tpl, other) is None
        # 授权 view
        session.add(TemplateGrant(template_id=tpl.id, user_id=2, permission="view"))
        await session.commit()
        assert await template_service.resolve_permission(session, tpl, other) == "view"


class TestListAccessible:
    async def test_filter_by_acl(self, session: AsyncSession) -> None:
        tpl, _ = await _base(session)
        owner = await session.get(User, 1)
        other = await session.get(User, 2)
        admin = User(
            id=9,
            username="admin",
            email="a@x.com",
            display_name="Admin",
            source="local",
            role="admin",
            is_active=True,
        )
        owner_pairs = await template_service.list_accessible(session, owner)
        assert [(t.id, p) for t, p in owner_pairs] == [(tpl.id, "manage")]

        # 无授权 -> 空
        assert await template_service.list_accessible(session, other) == []

        session.add(TemplateGrant(template_id=tpl.id, user_id=2, permission="edit"))
        await session.commit()
        other_pairs = await template_service.list_accessible(session, other)
        assert [(t.id, p) for t, p in other_pairs] == [(tpl.id, "edit")]

        admin_pairs = await template_service.list_accessible(session, admin)
        assert any(t.id == tpl.id for t, _ in admin_pairs)
