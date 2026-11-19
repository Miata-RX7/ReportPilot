#!/bin/sh
cd /opt/reportpilot
echo "=== 报告 110 状态 ==="
docker compose exec -T db psql -U reportpilot -d reportpilot -x -c "
SELECT id, status, confirmation_token IS NULL AS token_cleared,
       confirmed_at, confirmed_no_push_at, pushed_at, expires_at
FROM reports WHERE id=110;" 2>&1 | tail -12
