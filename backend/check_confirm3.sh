#!/bin/sh
cd /opt/reportpilot
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -16
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
        r = await db.get(Report, 105)
        print("报告 105 status =", r.status)
        print("  有 token =", bool(r.confirmation_token), " expires_at =", r.expires_at)
        print("  created  =", r.created_at)
    # 用 resend 重新发（会生成新 token）
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/reports/105/resend-confirmation",
        headers={"Authorization": "Bearer " + token}, method="POST")
    try:
        rr = urllib.request.urlopen(req, timeout=60)
        print("resend ->", rr.status, rr.read().decode()[:200])
    except urllib.error.HTTPError as e:
        print("resend ->", e.code, e.read().decode()[:200])
asyncio.run(main())
PYEOF
echo
echo "=== 查邮件/日志里的明文 token（后端发信时通常会记）==="
docker compose logs backend --tail 200 2>&1 | grep -oE "token=[A-Za-z0-9_]+" | tail -3
docker compose logs backend --tail 200 2>&1 | grep -iE "确认链接|confirmation" | tail -4
