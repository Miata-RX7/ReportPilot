#!/bin/sh
# 开发测试环境：真正同步代码后重建 + 验证（改成 6 小时看是否真存住）
set -e
cd /opt/reportpilot

echo "=== 1) 重建并重启 ==="
docker compose build backend frontend 2>&1 | tail -2
docker compose up -d backend frontend 2>&1 | tail -2
sleep 20

echo "=== 2) 验证：改成 6 小时 -> 读回 -> 改回 24 ==="
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -14
import asyncio
import json
import urllib.request

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.app_setting import AppSetting
from app.models.user import User
from app.services.security import create_token


async def main():
    async with AsyncSessionLocal() as db:
        u = (await db.scalars(select(User).where(User.username == "admin"))).first()
        token = create_token(u.id, u.username, u.role)

    def get():
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/system/settings",
            headers={"Authorization": "Bearer " + token},
        )
        return json.loads(urllib.request.urlopen(req, timeout=30).read())

    def put(hours):
        d = get()
        payload = json.dumps(
            {
                "smtp_host": d.get("smtp_host", ""),
                "smtp_port": d.get("smtp_port", 465),
                "smtp_username": d.get("smtp_username", ""),
                "smtp_from": d.get("smtp_from", ""),
                "smtp_use_tls": d.get("smtp_use_tls", True),
                "app_base_url": d.get("app_base_url", ""),
                "reminder_interval_hours": hours,
            }
        ).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/system/settings/mail",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + token,
            },
            method="PUT",
        )
        return json.loads(urllib.request.urlopen(req, timeout=30).read())

    print(f"  初始 GET          -> {get().get('reminder_interval_hours')}")
    print(f"  PUT 6 小时        -> {put(6).get('reminder_interval_hours')}")
    print(f"  再 GET 确认存住   -> {get().get('reminder_interval_hours')}")
    async with AsyncSessionLocal() as db:
        rows = (
            await db.scalars(
                select(AppSetting).where(
                    AppSetting.key == "reminder_interval_hours"
                )
            )
        ).all()
        print(f"  DB 实际值         -> {[r.value for r in rows] or '未落库'}")
    print(f"  改回 24           -> {put(24).get('reminder_interval_hours')}")
    print(f"  越界 0 应 422     -> ", end="")
    try:
        put(0)
        print("!! 未被拒绝")
    except Exception as e:
        print(f"已拒绝 ({getattr(e, 'code', '?')})")


asyncio.run(main())
PYEOF
