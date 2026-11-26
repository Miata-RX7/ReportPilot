"""验证硬删除 + 同名可重建。"""

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


async def api(path, token, method="GET", body=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method=method,
    )
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, (r.read().decode() or "")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


async def main():
    async with AsyncSessionLocal() as db:
        admin = (await db.scalars(select(User).where(User.username == "admin"))).first()
        token = create_token(admin.id, admin.username, admin.role)
        m = (
            await db.scalars(
                select(MetricDefinition).where(MetricDefinition.id == 1)
            )
        ).first()
        print(f"=== 目标：id=1 {m.name} owner={m.owner_id} is_active={m.is_active} ===")
        name = m.name

    print("\n=== 1) DELETE（硬删）===")
    code, body = await api("/api/metrics/1", token, "DELETE")
    print(f"  -> {code}")

    async with AsyncSessionLocal() as db:
        m = await db.get(MetricDefinition, 1)
        print(f"  库里 id=1 现在: {m}   ← None = 真删了")

    print("\n=== 2) 再建同名指标（应成功，不再提示已存在）===")
    code, body = await api(
        "/api/metrics",
        token,
        "POST",
        {
            "name": name,
            "metric_type": "scalar",
            "data_source_id": 1,
            "query_config": {"host_group_ids": [], "host_ids": [], "item_ids": []},
            "unit": "bps",
            "tags": ["同名验证"],
        },
    )
    print(f"  -> {code} {body[:160]}")
    new_id = None
    if code in (200, 201):
        new_id = json.loads(body).get("id")

    print("\n=== 3) 清理（把新建的删掉）===")
    if new_id:
        code, _ = await api(f"/api/metrics/{new_id}", token, "DELETE")
        print(f"  删除新建的 id={new_id} -> {code}")
    async with AsyncSessionLocal() as db:
        m = await db.get(MetricDefinition, new_id) if new_id else None
        print(f"  库里 id={new_id}: {m}   ← None = 清理干净")


asyncio.run(main())
