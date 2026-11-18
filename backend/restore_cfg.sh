#!/bin/sh
cd /opt/reportpilot
echo "=== 恢复数据源 timeout（原值 30）==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -c "
UPDATE data_sources
SET config_json = jsonb_set(config_json::jsonb, '{timeout}', '30'::jsonb)
WHERE id IN (3,9) AND (config_json->>'timeout')::int <> 30;
SELECT id, name, config_json->>'timeout' AS timeout FROM data_sources ORDER BY id;" 2>&1 | tail -8
echo
echo "=== 恢复后平台接口仍然正常 ==="
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -6
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
            ("分页 第1页", "/api/metrics/paged?page=1&page_size=10&status=all"),
            ("分页 第5页", "/api/metrics/paged?page=5&page_size=10&status=all"),
            ("全量(旧接口)", "/api/metrics?include_inactive=true"),
        ]:
            t0=time.time()
            r = await c.get(path, headers=h)
            d=r.json()
            n = len(d["items"]) if isinstance(d,dict) and "items" in d else (len(d) if isinstance(d,list) else "-")
            print(f"  {label:16} {r.status_code} {time.time()-t0:6.2f}s rows={n}")
asyncio.run(main())
PYEOF
echo
echo "=== 顺带确认：模板 19 当前版本还有哪些变量未绑定 ==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT v.id AS version_id, p.placeholder
FROM template_versions v
CROSS JOIN LATERAL jsonb_array_elements_text(v.placeholders_json) p(placeholder)
WHERE v.id = (SELECT current_version_id FROM word_templates WHERE id=19)
  AND NOT EXISTS (
    SELECT 1 FROM template_bindings b
    WHERE b.version_id = v.id AND b.placeholder = p.placeholder
  )
ORDER BY p.placeholder;" 2>&1 | tail -10
