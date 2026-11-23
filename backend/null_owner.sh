#!/bin/sh
cd /opt/reportpilot
echo "=== owner_id IS NULL 的指标（生产）==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT m.id, m.name, m.metric_type, m.is_active, m.created_at::date,
       (SELECT count(*) FROM template_bindings b WHERE b.binding_ref_id=m.id) AS 绑定数,
       (SELECT count(*) FROM metric_definitions c WHERE c.metric_type='calculated'
          AND c.query_config->'variables' @> jsonb_build_array(jsonb_build_object('metric_id', m.id))) AS 被计算型引用
FROM metric_definitions m WHERE m.owner_id IS NULL ORDER BY m.id;"
echo
echo "=== 统计 ==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT '无主指标总数', count(*) FROM metric_definitions WHERE owner_id IS NULL
UNION ALL SELECT '  其中活跃', count(*) FROM metric_definitions WHERE owner_id IS NULL AND is_active
UNION ALL SELECT '  其中停用', count(*) FROM metric_definitions WHERE owner_id IS NULL AND NOT is_active
UNION ALL SELECT '有主指标', count(*) FROM metric_definitions WHERE owner_id IS NOT NULL
UNION ALL SELECT '按 owner 分布:', string_agg(u.username || '=' || c.n, ', ' ORDER BY c.n DESC)
  FROM (SELECT owner_id, count(*) n FROM metric_definitions WHERE owner_id IS NOT NULL GROUP BY owner_id) c
  JOIN users u ON u.id=c.owner_id;"
echo
echo "=== 模板的 owner 是否也有 NULL ==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT '模板 owner_id IS NULL', count(*) FROM word_templates WHERE owner_id IS NULL
UNION ALL SELECT '任务 created_by IS NULL', count(*) FROM report_tasks WHERE created_by IS NULL;"
