#!/bin/sh
cd /opt/reportpilot
echo "=== 报告记录 + 失败原因 ==="
docker compose exec -T db psql -U reportpilot -d reportpilot -x -c "
SELECT r.id, r.task_id, r.period, r.status, r.source, r.created_at, r.error_stage, r.last_error
FROM reports r WHERE r.task_id=21 ORDER BY r.id DESC LIMIT 3;" 2>&1 | tail -30
echo
echo "=== 模板 19 当前版本 + 未绑定变量 ==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT '模板', t.id, t.name, '当前版本', t.current_version_id
FROM word_templates t WHERE t.id=19;
SELECT '版本', v.id, v.version_no, v.is_active FROM template_versions v WHERE v.id=(SELECT current_version_id FROM word_templates WHERE id=19);
SELECT '未绑定变量', p.placeholder
FROM template_versions v
CROSS JOIN LATERAL jsonb_array_elements_text(v.placeholders_json) p(placeholder)
WHERE v.id=(SELECT current_version_id FROM word_templates WHERE id=19)
  AND NOT EXISTS (SELECT 1 FROM template_bindings b WHERE b.version_id=v.id AND b.placeholder=p.placeholder)
ORDER BY 1;" 2>&1 | tail -20
echo
echo "=== 模板 19 当前版本的全部绑定 ==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT b.placeholder, b.binding_type, b.binding_ref_id, m.name, m.is_active, m.owner_id
FROM template_bindings b
JOIN template_versions v ON v.id=b.version_id
LEFT JOIN metric_definitions m ON m.id=b.binding_ref_id
WHERE v.id=(SELECT current_version_id FROM word_templates WHERE id=19)
ORDER BY b.placeholder;" 2>&1 | tail -25
