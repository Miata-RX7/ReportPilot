#!/bin/sh
cd /opt/reportpilot
echo "=== 所有以 __ 开头的测试指标（活跃 + 停用）==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT m.id, m.name, m.metric_type, m.is_active, m.owner_id,
       (SELECT count(*) FROM template_bindings b
         WHERE b.binding_type='metric' AND b.binding_ref_id=m.id) AS 模板绑定数,
       (SELECT count(*) FROM metric_definitions c
         WHERE c.metric_type='calculated' AND c.is_active
           AND c.query_config->'variables' @> jsonb_build_array(jsonb_build_object('metric_id', m.id))) AS 被计算型引用数
FROM metric_definitions m
WHERE m.name LIKE '\_\_%'
ORDER BY m.id;" 2>&1 | tail -40
echo
echo "=== 统计 ==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT '总数', count(*) FROM metric_definitions WHERE name LIKE '\_\_%'
UNION ALL SELECT '活跃', count(*) FROM metric_definitions WHERE name LIKE '\_\_%' AND is_active
UNION ALL SELECT '停用', count(*) FROM metric_definitions WHERE name LIKE '\_\_%' AND NOT is_active
UNION ALL SELECT '正常指标总数(排除__)', count(*) FROM metric_definitions WHERE name NOT LIKE '\_\_%';" 2>&1 | tail -6
