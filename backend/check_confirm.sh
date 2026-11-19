#!/bin/sh
cd /opt/reportpilot
echo "=== 确认页 preview（应 valid + 带 no_data_vars）==="
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -12
import asyncio, json, urllib.request, urllib.error
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.report import Report
from app.models.user import User
from app.services.security import create_token

async def main():
    async with AsyncSessionLocal() as db:
        u = (await db.scalars(select(User).where(User.username == "337"))).first()
        token = create_token(u.id, u.username, u.role)
        r = await db.get(Report, 104)
        ct = r.confirmation_token
        print("报告 104 status =", r.status, " 有 token =", bool(ct))
        if not ct:
            print("!! 没有确认 token（可能已确认过）")
            return
    req = urllib.request.Request(
        f"http://127.0.0.1:8000/api/confirm/104?token={ct}")
    d = json.loads(urllib.request.urlopen(req, timeout=60).read())
    print("token_state =", d.get("token_state"), " message =", d.get("message"))
    print("status      =", d.get("status"))
    nd = d.get("no_data_vars") or []
    print(f"no_data_vars ({len(nd)} 条):")
    for x in nd:
        print(f"   - {x.get('placeholder')} | {x.get('metric_name')}")
    # 试「确认并发送」-> 应被状态机拒绝
    req2 = urllib.request.Request(
        "http://127.0.0.1:8000/api/confirm/104",
        data=json.dumps({"token": ct}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        method="POST")
    try:
        rr = urllib.request.urlopen(req2, timeout=60)
        print("!! 确认并发送竟然成功:", rr.status, rr.read()[:200])
    except urllib.error.HTTPError as e:
        print("确认并发送 ->", e.code, e.read().decode()[:160])
asyncio.run(main())
PYEOF
