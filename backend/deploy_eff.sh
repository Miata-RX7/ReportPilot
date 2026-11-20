#!/bin/sh
cd /opt/reportpilot
docker compose build backend frontend 2>&1 | tail -1
docker compose up -d --force-recreate backend frontend 2>&1 | tail -1
sleep 20
echo "=== 效率价值接口 ==="
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -22
import asyncio, json, urllib.request
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.security import create_token


async def main():
    async with AsyncSessionLocal() as db:
        u = (await db.scalars(select(User).where(User.username == "admin"))).first()
        token = create_token(u.id, u.username, u.role)
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/efficiency/value",
        headers={"Authorization": "Bearer " + token},
    )
    d = json.loads(urllib.request.urlopen(req, timeout=60).read())
    s = d["summary"]
    print(
        f"汇总: 任务 {s['task_count']} 个 | 执行 {s['run_count']} 次 | "
        f"人工 {s['total_manual_minutes']} 分 | 平台 {s['total_platform_minutes']} 分 | "
        f"节省 {s['total_saved_minutes']} 分"
    )
    for t in d["tasks"]:
        print(
            f"  - {t['task_name']}: 数值×{t['scalar_count']} 曲线×{t['curve_count']} "
            f"摘要×{t['insight_count']} Excel={t['has_excel_summary']} | "
            f"执行 {t['run_count']} 次 | 人工每次 {t['manual_per_run']} 分 | "
            f"平台一次性 {t['platform_setup']} 分 | 每次省 {t['saved_per_run']} 分 | "
            f"累计省 {t['total_saved']} 分 | 回本 {t['breakeven_runs']} 次"
        )
    print("mine 任务数:", len(d["mine"]))


asyncio.run(main())
PYEOF
echo
echo "=== 标签筛选实测（OR 语义）==="
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -6
import asyncio, httpx
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.security import create_token


async def main():
    async with AsyncSessionLocal() as db:
        u = (await db.scalars(select(User).where(User.username == "admin"))).first()
        t = create_token(u.id, u.username, u.role)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=60) as c:
        for q in ["", "&tag=%E8%92%99%E5%8F%A4%E5%9B%BD%E8%88%AA",
                  "&tag=%E8%92%99%E5%8F%A4%E5%9B%BD%E8%88%AA&tag=%E6%B5%B7%E6%B2%B9"]:
            r = await c.get(
                f"/api/metrics/paged?page=1&page_size=10&status=all{q}",
                headers={"Authorization": "Bearer " + t},
            )
            d = r.json()
            print(f"{q or '(无)'} -> total={d.get('total')} tags={d.get('available_tags')}")


asyncio.run(main())
PYEOF
