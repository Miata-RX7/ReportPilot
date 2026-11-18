#!/bin/sh
cd /opt/reportpilot
echo "=== 硬删除所有 __ 前缀的测试指标 ==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -c "
DELETE FROM metric_definitions
WHERE name LIKE '\_\_%'
  AND is_active = false
  AND id NOT IN (SELECT binding_ref_id FROM template_bindings WHERE binding_type='metric' AND binding_ref_id IS NOT NULL);
" 2>&1 | tail -3
echo
echo "=== 清理后核对 ==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT '剩余 __ 前缀指标', count(*) FROM metric_definitions WHERE name LIKE '\_\_%'
UNION ALL SELECT '指标总数', count(*) FROM metric_definitions
UNION ALL SELECT '活跃指标', count(*) FROM metric_definitions WHERE is_active
UNION ALL SELECT '停用指标', count(*) FROM metric_definitions WHERE NOT is_active;" 2>&1 | tail -6
echo
echo "=== 还剩哪些停用指标（应当只剩真实的历史数据）==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT id, name, owner_id FROM metric_definitions WHERE NOT is_active ORDER BY id;" 2>&1 | tail -15
echo
echo "=== 平台接口复查（列表应无残留）==="
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -5
import asyncio, time, httpx
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.security import create_token
async def main():
    async with AsyncSessionLocal() as db:
        u = (await db.scalars(select(User).where(User.username=="admin"))).first()
        token = create_token(u.id, u.username, u.role)
    h = {"Authorization": "Bearer " + token}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=60) as c:
        for label, path in [
            ("分页 status=all", "/api/metrics/paged?page=1&page_size=10&status=all"),
            ("分页 status=inactive", "/api/metrics/paged?page=1&page_size=10&status=inactive"),
        ]:
            t0=time.time()
            r = await c.get(path, headers=h)
            d=r.json()
            names = [i["name"] for i in d.get("items",[])]
            tmp = [n for n in names if n.startswith("__")]
            print(f"  {label:22} {r.status_code} {time.time()-t0:5.2f}s total={d.get('total')} 本页__开头={tmp}")
asyncio.run(main())
PYEOF
