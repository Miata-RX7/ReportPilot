#!/bin/sh
cd /opt/reportpilot
echo "=== 337 名下的报告任务 ==="
docker compose exec -T db psql -U reportpilot -d reportpilot -x -c "
SELECT t.id, t.name, t.template_id, t.cron_expr, t.period_granularity, t.period_offset,
       t.confirmer_email, t.recipient_emails, t.created_by, t.is_active
FROM report_tasks t JOIN users u ON u.id=t.created_by
WHERE u.username='337' ORDER BY t.id;" 2>&1 | tail -30
echo
echo "=== 该任务的报告记录（最近 5 条）==="
docker compose exec -T db psql -U reportpilot -d reportpilot -x -c "
SELECT r.id, r.task_id, r.period, r.status, r.source, r.created_at, r.last_error, r.error_message
FROM reports r JOIN report_tasks t ON t.id=r.task_id JOIN users u ON u.id=t.created_by
WHERE u.username='337' ORDER BY r.id DESC LIMIT 5;" 2>&1 | tail -40
echo
echo "=== 报告表有哪些 error 相关列 ==="
docker compose exec -T db psql -U reportpilot -d reportpilot -t -A -c "
SELECT column_name FROM information_schema.columns WHERE table_name='reports' AND (column_name LIKE '%error%' OR column_name LIKE '%fail%' OR column_name LIKE '%message%');" 2>&1 | tail -8
