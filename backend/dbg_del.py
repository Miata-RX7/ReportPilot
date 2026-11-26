"""实测：删除自己的指标为什么失败。"""

import asyncio
import json
import urllib.error
import urllib.request

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.metric import MetricDefinition
from app.models.user import User
from app.services.security import create_token

BASE = "http://127.0.0.1:8000"


async def api(path, token, method="GET"):
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method=method,
    )
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, (r.read().decode() or "")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


async def main():
    async with AsyncSessionLocal() as db:
        u = (await db.scalars(select(User).where(User.username == "admin"))).first()
        token = create_token(u.id, u.username, u.role)
        u337 = (await db.scalars(select(User).where(User.username == "337"))).first()
        t337 = create_token(u337.id, u337.username, u337.role)
        rows = list(
            (
                await db.scalars(
                    select(MetricDefinition)
                    .where(MetricDefinition.id.in_([1, 2, 3]))
                    .order_by(MetricDefinition.id)
                )
            ).all()
        )
        for m in rows:
            print(f"  id={m.id} {m.name} owner={m.owner_id} active={m.is_active} tags={m.tags}")

    print("\n=== 删除前检查（1 号）===")
    print("  referenced-by ->", await api("/api/metrics/1/referenced-by", token))
    print("  bindings      ->", await api("/api/metrics/1/bindings", token))

    print("\n=== 直接 DELETE 1 号（admin）===")
    code, body = await api("/api/metrics/1", token, "DELETE")
    print(f"  -> {code} {body[:200]}")

    async with AsyncSessionLocal() as db:
        m = await db.get(MetricDefinition, 1)
        print(f"  删除后：id=1 is_active={m.is_active}（软删则 False，硬删则 None）")

    print("\n=== 再次 DELETE 1 号（模拟用户「点了好几遍」）===")
    code, body = await api("/api/metrics/1", token, "DELETE")
    print(f"  -> {code} {body[:200]}")

    print("\n=== 337 用户 DELETE 自己的指标（1 号若归 337）===")
    async with AsyncSessionLocal() as db:
        m = await db.get(MetricDefinition, 1)
        owner = m.owner_id if m else None
    if owner == u337.id:
        code, body = await api("/api/metrics/1", t337, "DELETE")
        print(f"  -> {code} {body[:200]}")
    else:
        print(f"  1 号的 owner={owner}，不是 337（{u337.id}）")


asyncio.run(main())
