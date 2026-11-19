#!/bin/sh
cd /opt/reportpilot
docker compose build backend frontend 2>&1 | tail -1
docker compose up -d --force-recreate backend frontend 2>&1 | tail -1
sleep 18
echo "=== 报告 110 再打开确认链接（token 已消费）==="
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -8
import asyncio, json, urllib.request
from app.database import AsyncSessionLocal
from app.models.report import Report
from app.services.task_service import hash_token

RAW = "TESTTOKEN_used_123"


async def main():
    async with AsyncSessionLocal() as db:
        r = await db.get(Report, 110)
        print("报告 110 status =", r.status, " token已清空 =", r.confirmation_token is None)
        # 用一个已知 token 模拟「已使用」：把 token 设成别的哈希 -> 链接无效
        # 真实场景是 token=None；这里直接构造 token=None 的情况
        r.confirmation_token = None
        await db.commit()


asyncio.run(main())

req = urllib.request.Request(
    f"http://127.0.0.1:8000/api/confirm/110?token=whatever")
d = json.loads(urllib.request.urlopen(req, timeout=60).read())
print("token_state =", d.get("token_state"))
print("message     =", d.get("message"))
print("status      =", d.get("status"))
PYEOF
