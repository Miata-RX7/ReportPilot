"""CLI 单元测试：reset-password（覆盖已设密码 / 空哈希 / 不存在 / 过短 / 不一致）。"""

import argparse

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import cli
from app.models import Base
from app.models.user import User
from app.services.security import hash_password, verify_password


@pytest_asyncio.fixture
async def session_factory(tmp_path, monkeypatch):
    """把 CLI 用的 AsyncSessionLocal 指向内存库。"""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(cli, "AsyncSessionLocal", factory)

    async with factory() as s:
        s.add_all(
            [
                User(
                    id=1,
                    username="ops",
                    email="ops@x.com",
                    display_name="ops",
                    source="local",
                    role="admin",
                    password_hash=hash_password("OldPass123"),
                ),
                # 历史种子账号：没有任何密码哈希
                User(
                    id=2,
                    username="admin",
                    email="admin@x.com",
                    display_name="管理员",
                    source="local",
                    role="admin",
                    password_hash=None,
                ),
            ]
        )
        await s.commit()
    yield factory
    await engine.dispose()


def _args(**kw) -> argparse.Namespace:
    base = {"username": "ops", "password": None}
    base.update(kw)
    return argparse.Namespace(**base)


class TestResetPassword:
    async def test_reset_existing_password(self, session_factory, capsys) -> None:
        code = await cli._reset_password(_args(password="NewPass456"))
        assert code == 0
        out = capsys.readouterr().out
        assert "密码已重置" in out and "ops" in out
        async with session_factory() as s:
            u = await s.scalar(select(User).where(User.username == "ops"))
            assert verify_password("NewPass456", u.password_hash)
            assert not verify_password("OldPass123", u.password_hash)

    async def test_fills_empty_hash(self, session_factory, capsys) -> None:
        """历史 admin 无哈希 -> 补写并提示『已设置』。"""
        code = await cli._reset_password(
            _args(username="admin", password="AdminPass123")
        )
        assert code == 0
        assert "密码已设置" in capsys.readouterr().out
        async with session_factory() as s:
            u = await s.scalar(select(User).where(User.username == "admin"))
            assert verify_password("AdminPass123", u.password_hash)

    async def test_unknown_user(self, session_factory, capsys) -> None:
        code = await cli._reset_password(
            _args(username="ghost", password="NewPass456")
        )
        assert code == 1
        assert "用户不存在" in capsys.readouterr().err

    async def test_too_short(self, session_factory, capsys) -> None:
        code = await cli._reset_password(_args(password="1234"))
        assert code == 1
        assert "密码至少" in capsys.readouterr().err

    async def test_interactive_mismatch(self, session_factory, monkeypatch, capsys) -> None:
        prompts = iter(["NewPass456", "OtherPass789"])
        monkeypatch.setattr(cli.getpass, "getpass", lambda *a, **k: next(prompts))
        code = await cli._reset_password(_args(password=None))
        assert code == 1
        assert "两次输入不一致" in capsys.readouterr().err

    async def test_interactive_success(self, session_factory, monkeypatch, capsys) -> None:
        prompts = iter(["NewPass456", "NewPass456"])
        monkeypatch.setattr(cli.getpass, "getpass", lambda *a, **k: next(prompts))
        assert await cli._reset_password(_args(password=None)) == 0
        assert "密码已重置" in capsys.readouterr().out

    async def test_password_not_leaked_to_output(
        self, session_factory, capsys
    ) -> None:
        await cli._reset_password(_args(password="Sup3rSecret!"))
        captured = capsys.readouterr()
        assert "Sup3rSecret!" not in captured.out
        assert "Sup3rSecret!" not in captured.err
