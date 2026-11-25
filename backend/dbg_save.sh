#!/bin/sh
cd /opt/reportpilot
echo "=== 1) 「拉米卡」相关模板 ==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT t.id, t.name, t.is_active, t.current_version_id,
       (SELECT count(*) FROM template_versions v WHERE v.template_id=t.id) AS 版本数,
       u.username AS owner
FROM word_templates t LEFT JOIN users u ON u.id=t.owner_id
WHERE t.name ILIKE '%拉米卡%';"

echo
echo "=== 2) 该模板的版本列表（最近 8 个）==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT v.id, v.version_no, v.created_at, v.created_by, u.username
FROM template_versions v
JOIN word_templates t ON t.id=v.template_id
LEFT JOIN users u ON u.id=v.created_by
WHERE t.name ILIKE '%拉米卡%'
ORDER BY v.version_no DESC LIMIT 8;"

echo
echo "=== 3) 后端日志：有没有 ONLYOFFICE 回调 ==="
docker compose logs backend --tail 400 2>&1 | grep -iE "callback|onlyoffice|editor|save|版本" | tail -20

echo
echo "=== 4) 后端日志：最近的 4xx/5xx ==="
docker compose logs backend --tail 400 2>&1 | grep -E " (4[0-9][0-9]|5[0-9][0-9]) " | tail -12

echo
echo "=== 5) 回调路由是否注册 ==="
docker compose exec -T backend python -c "
from app.main import app
for p in sorted(app.openapi()['paths']):
    if any(k in p for k in ('onlyoffice','editor','callback','save')):
        print('  ', p, list(app.openapi()['paths'][p].keys()))
" 2>&1 | tail -12
