"""生产：曲线指标的 calc_formula 与列表接口返回值。"""

import asyncio
import json
import urllib.request

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.metric import MetricDefinition
from app.models.user import User
from app.services.security import create_token


async def main():
    async with AsyncSessionLocal() as db:
        u = (await db.scalars(select(User).where(User.username == "admin"))).first()
        token = create_token(u.id, u.username, u.role)
        rows = list(
            (
                await db.scalars(
                    select(MetricDefinition)
                    .where(MetricDefinition.metric_type == "curve")
                    .order_by(MetricDefinition.created_at.desc())
                    .limit(5)
                )
            ).all()
        )
        print("=== 库里的曲线指标 ===")
        for m in rows:
            print(f"  id={m.id} {m.name[:24]:26} active={m.is_active} calc={m.calc_formula}")

    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/metrics/paged?page=1&page_size=50&status=all",
        headers={"Authorization": "Bearer " + token},
    )
    d = json.loads(urllib.request.urlopen(req, timeout=180).read())
    curves = [m for m in d["items"] if m["metric_type"] == "curve"]
    print(f"\n=== 列表接口返回的曲线指标（{len(curves)} 条）===")
    for m in curves[:5]:
        print(f"  id={m['id']} {m['name'][:22]:24} calc_formula={m.get('calc_formula')}")


asyncio.run(main())
