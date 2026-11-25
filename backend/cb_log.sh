#!/bin/sh
cd /opt/reportpilot
echo "=== ONLYOFFICE 回调日志（新增的可观测性）==="
docker compose logs backend --since 10m --no-log-prefix 2>&1 | grep -iE "ONLYOFFICE" | grep -v "INFO:     172" | tail -15
echo
echo "=== 回调 access log ==="
docker compose logs backend --since 10m --no-log-prefix 2>&1 | grep "callback" | tail -6
echo
echo "=== DS 侧：保存回调的结果 ==="
docker compose logs onlyoffice --since 10m --no-log-prefix 2>&1 | grep -iE "sendServerRequest|storeForgotten|error" | tail -8
docker compose exec -T onlyoffice sh -c "grep -iE 'sendServerRequest|storeForgotten' /var/log/onlyoffice/documentserver/docservice/out.log 2>/dev/null | tail -5"
echo
echo "=== 模板 20 的版本列表（有没有新版本）==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -F " | " -c "
SELECT v.id, v.version_no, v.created_at, v.source, v.created_by
FROM template_versions v JOIN word_templates t ON t.id=v.template_id
WHERE t.id=20 ORDER BY v.version_no DESC LIMIT 5;"
