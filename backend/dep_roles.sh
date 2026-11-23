#!/bin/sh
# 开发测试环境：部署角色组后端 + 迁移 + 验证
set -e
cd /opt/reportpilot

echo "=== 1) 重建并重启 ==="
docker compose build backend 2>&1 | tail -2
docker compose up -d backend 2>&1 | tail -2
sleep 20

echo "=== 2) 迁移（建 roles/role_members + owner_id NOT NULL）==="
docker compose exec -T backend alembic upgrade head 2>&1 | grep -iE "upgrade|error" | tail -4
docker compose exec -T backend alembic current 2>&1 | tail -1

echo "=== 3) 表结构核对 ==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT 'roles', count(*) FROM roles
UNION ALL SELECT 'role_members', count(*) FROM role_members;"
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -c "
SELECT 'owner_id 可空=' || is_nullable FROM information_schema.columns
WHERE table_name='metric_definitions' AND column_name='owner_id';"

echo "=== 4) 端到端：建角色 -> 加成员 -> 普通用户可见对方指标 ==="
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -16
import asyncio
import json
import urllib.request
import urllib.error

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.security import create_token


async def api(path, token, method="GET", body=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        "http://127.0.0.1:8000" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=h, method=method,
    )
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:160]


async def main():
    async with AsyncSessionLocal() as db:
        admin = (await db.scalars(select(User).where(User.username == "admin"))).first()
        at = create_token(admin.id, admin.username, admin.role)
        u337 = (await db.scalars(select(User).where(User.username == "337"))).first()
        t337 = create_token(u337.id, u337.username, u337.role)
        uid337 = u337.id

    # 1) 建角色
    code, d = await api("/api/roles", at, "POST", {"name": "验证角色组", "description": "自动化验证"})
    print(f"  建角色 -> {code} {str(d)[:80]}")
    rid = d["id"] if code == 201 else None
    if not rid:
        return

    # 2) 加成员（admin + 337）
    code, d = await api(f"/api/roles/{rid}/members", at, "POST", {"user_ids": [1, uid337]})
    print(f"  加成员 -> {code} {[m['username'] for m in d] if code == 200 else d}")

    # 3) 337 登录后看角色列表（应只看到自己加入的）
    code, d = await api("/api/roles", t337)
    print(f"  337 看角色列表 -> {code} {[r['name'] for r in d] if code == 200 else d}")

    # 4) 337 看 admin 的指标（应可见 + 只读标记）
    code, d = await api("/api/metrics/paged?page=1&page_size=5&status=all", t337)
    if code == 200:
        own = sum(1 for m in d["items"] if (m.get("ownership") or {}).get("is_own"))
        shared = sum(1 for m in d["items"] if (m.get("ownership") or {}).get("read_only"))
        print(f"  337 看指标 -> total={d['total']} 本页我的={own} 共享只读={shared}")
        for m in d["items"][:3]:
            o = m.get("ownership") or {}
            print(f"     {m['name'][:24]:26} is_own={o.get('is_own')} roles={o.get('shared_by_roles')} ro={o.get('read_only')}")

    # 5) 清理
    code, _ = await api(f"/api/roles/{rid}", at, "DELETE")
    print(f"  删角色 -> {code}")


asyncio.run(main())
PYEOF
