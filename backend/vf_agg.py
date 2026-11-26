"""验证：曲线指标的 agg（时段聚合）是否真的生效。

对同一个曲线指标，分别用 agg=max / avg 试算，对比结果。
"""

import asyncio
import json
import urllib.request

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.metric import MetricDefinition
from app.models.user import User
from app.services.security import create_token


async def compute(metric_id: int, token: str, period: str) -> dict:
    body = json.dumps({"period": period}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:8000/api/metrics/{metric_id}/compute",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        method="POST",
    )
    try:
        r = urllib.request.urlopen(req, timeout=180)
        return json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}


def max_of(d: dict) -> float:
    best = 0.0
    for s in (d.get("value") or {}).get("series") or []:
        for p in s.get("points") or []:
            if len(p) >= 2 and p[1] is not None:
                best = max(best, float(p[1]))
    return best


async def main():
    async with AsyncSessionLocal() as db:
        u = (await db.scalars(select(User).where(User.username == "admin"))).first()
        token = create_token(u.id, u.username, u.role)
        m = (
            await db.scalars(
                select(MetricDefinition).where(MetricDefinition.id == 57)
            )
        ).first()
        print(f"=== 指标 57：{m.name} ===")
        print(f"  存的 calc_formula = {m.calc_formula}")
        print(f"  存的 type_config  = {m.type_config}")

        for agg in ("max", "avg"):
            m.calc_formula = {**(m.calc_formula or {}), "agg": agg}
            await db.commit()
            await db.refresh(m)
            d = await compute(57, token, "2026-10")
            if "error" in d:
                print(f"  agg={agg:4} -> 计算失败: {d['error'][:90]}")
                continue
            pts = sum(len(s["points"]) for s in (d.get("value") or {}).get("series") or [])
            print(
                f"  agg={agg:4} -> 点数={pts:4d}  各线最大值={max_of(d):.4f}  "
                f"formula={(d.get('formula') or '')[:70]}"
            )

        # 复原
        m.calc_formula = {**(m.calc_formula or {}), "agg": "max"}
        await db.commit()
        print("  已复原为 agg=max")


asyncio.run(main())
