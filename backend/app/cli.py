"""管理 CLI。

用法（容器内）：
  python -m app.cli create-user --username ops --role admin --email ops@x.com
  python -m app.cli reset-password --username ops
  python -m app.cli issue-token --username ops --days 7

reset-password 用于忘记密码时重置；默认交互式输入两遍（不回显、不进 shell
history），仅在自动化脚本里才用 --password（会进入 shell history）。
重置只改密码，不改角色/启用状态。

issue-token 为 Phase 1 临时方案（前端登录页 Phase 2 才做），
登录页上线后移除该子命令。
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.security import MIN_PASSWORD_LENGTH, create_token, hash_password


async def _create_user(args: argparse.Namespace) -> int:
    if args.role not in ("admin", "user"):
        print("role 必须是 admin 或 user", file=sys.stderr)
        return 1
    password = getpass.getpass("Password: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"密码至少 {MIN_PASSWORD_LENGTH} 位", file=sys.stderr)
        return 1
    if getpass.getpass("Confirm: ") != password:
        print("两次输入不一致", file=sys.stderr)
        return 1
    async with AsyncSessionLocal() as session:
        exists = await session.scalar(
            select(User.id).where(User.username == args.username)
        )
        if exists is not None:
            print(f"用户名已存在: {args.username}", file=sys.stderr)
            return 1
        user = User(
            username=args.username,
            email=args.email or f"{args.username}@local",
            display_name=args.display_name or args.username,
            source="local",
            role=args.role,
            password_hash=hash_password(password),
        )
        session.add(user)
        await session.commit()
        print(f"用户已创建 id={user.id} username={user.username} role={user.role}")
        return 0


async def _reset_password(args: argparse.Namespace) -> int:
    """重置用户密码（只改密码，不改角色/启用状态）。

    兼容历史上 password_hash 为空（如种子 admin）的账号：直接补写哈希。
    """
    password = args.password
    if not password:
        password = getpass.getpass("New password: ")
        if getpass.getpass("Confirm: ") != password:
            print("两次输入不一致", file=sys.stderr)
            return 1
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"密码至少 {MIN_PASSWORD_LENGTH} 位", file=sys.stderr)
        return 1

    async with AsyncSessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.username == args.username)
        )
        if user is None:
            print(f"用户不存在: {args.username}", file=sys.stderr)
            return 1
        had_password = bool(user.password_hash)
        user.password_hash = hash_password(password)
        await session.commit()
        action = "已重置" if had_password else "已设置"
        status = "启用" if user.is_active else "停用"
        print(
            f"密码{action} username={user.username} role={user.role} 状态={status}"
            "（密码不回显，未写入日志）"
        )
        return 0


async def _issue_token(args: argparse.Namespace) -> int:
    """打印 JWT（Phase 1 临时方案，Phase 2 登录页上线后移除）。"""
    async with AsyncSessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.username == args.username)
        )
        if user is None or not user.is_active:
            print(f"用户不存在或已停用: {args.username}", file=sys.stderr)
            return 1
        print(create_token(user.id, user.username, user.role, expire_days=args.days))
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="reportpilot-cli")
    sub = parser.add_subparsers(dest="command", required=True)

    cu = sub.add_parser("create-user", help="创建用户（密码交互式输入）")
    cu.add_argument("--username", required=True)
    cu.add_argument("--role", default="user", choices=["admin", "user"])
    cu.add_argument("--email", default=None)
    cu.add_argument("--display-name", default=None)

    it = sub.add_parser("issue-token", help="打印 JWT（Phase 1 临时方案）")
    it.add_argument("--username", required=True)
    it.add_argument("--days", type=int, default=7)

    rp = sub.add_parser(
        "reset-password", help="重置用户密码（默认交互式输入，不回显）"
    )
    rp.add_argument("--username", required=True)
    rp.add_argument(
        "--password",
        default=None,
        help="非交互传入（会进入 shell history，仅脚本用）",
    )

    args = parser.parse_args()
    if args.command == "create-user":
        return asyncio.run(_create_user(args))
    if args.command == "reset-password":
        return asyncio.run(_reset_password(args))
    return asyncio.run(_issue_token(args))


if __name__ == "__main__":
    sys.exit(main())
