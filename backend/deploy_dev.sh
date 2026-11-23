#!/bin/sh
# 开发测试环境部署 + 验证确认邮件频率
set -e
cd /opt/reportpilot

echo "=== 1) 拉代码 ==="
git pull --ff-only 2>&1 | tail -3

echo "=== 2) 构建并重启 ==="
docker compose build backend frontend 2>&1 | tail -2
docker compose up -d backend frontend 2>&1 | tail -2
sleep 20

echo "=== 3) 容器状态 ==="
docker compose ps --format '{{.Service}}\t{{.Status}}'

echo "=== 4) 验证：读取当前频率 ==="
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -12
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
        rows = (
            await db.scalars(
                select(AppSetting).where(
                    AppSetting.key == "reminder_interval_hours"
                )
            )
        ).all()
        print(f"  DB 里的 reminder_interval_hours = {[r.value for r in rows] or '未设置（用默认 24）'}")

    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/system/settings",
        headers={"Authorization": "Bearer " + token},
    )
    d = json.loads(urllib.request.urlopen(req, timeout=30).read())
    print(f"  GET /api/system/settings -> reminder_interval_hours = {d.get('reminder_interval_hours')}")

    # 改一个值试试（改回 24）
    payload = json.dumps(
        {
            "smtp_host": d.get("smtp_host", ""),
            "smtp_port": d.get("smtp_port", 465),
            "smtp_username": d.get("smtp_username", ""),
            "smtp_from": d.get("smtp_from", ""),
            "smtp_use_tls": d.get("smtp_use_tls", True),
            "app_base_url": d.get("app_base_url", ""),
            "reminder_interval_hours": 24,
        }
    ).encode()
    req2 = urllib.request.Request(
        "http://127.0.0.1:8000/api/system/settings/mail",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        method="PUT",
    )
    d2 = json.loads(urllib.request.urlopen(req2, timeout=30).read())
    print(f"  PUT /api/system/settings/mail (24h) -> {d2.get('reminder_interval_hours')}")


asyncio.run(main())
PYEOF
