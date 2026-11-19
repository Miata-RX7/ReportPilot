#!/bin/sh
cd /opt/reportpilot
echo "=== 同名指标（不同 owner，活跃）==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT m.name, m.owner_id, u.username, m.id
FROM metric_definitions m JOIN users u ON u.id=m.owner_id
WHERE m.is_active AND m.name IN (
  SELECT name FROM metric_definitions WHERE is_active GROUP BY name HAVING count(*) > 1
)
ORDER BY m.name, m.id;"
echo
echo "=== ops 名下与 337 同名的指标（就是会显示后缀的）==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT a.name, a.id AS id_337, b.id AS id_ops, b.name AS ops_name
FROM metric_definitions a JOIN metric_definitions b ON a.name=b.name AND a.owner_id=488 AND b.owner_id=2
WHERE a.is_active AND b.is_active ORDER BY a.id;"
