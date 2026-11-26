#!/bin/sh
# 生产：备份 + git 同步到最新
set -e
cd /opt/reportpilot
STAMP=$(date +%Y%m%d_%H%M)
BK=/data/reportpilot/backup/$STAMP
mkdir -p "$BK"

echo "=== 1) 备份 ==="
docker compose exec -T db pg_dump -U reportpilot -d reportpilot --clean --if-exists > "$BK/db.sql"
cp .env "$BK/env.prod"
docker tag reportpilot/backend:latest reportpilot/backend:prev-$STAMP
docker tag reportpilot/frontend:latest reportpilot/frontend:prev-$STAMP
ls -lh "$BK/db.sql" | awk '{print "  db.sql", $5}'
echo "  回滚镜像: prev-$STAMP"

echo
echo "=== 2) 同步前的 HEAD ==="
git log --oneline -1
echo "  差异 commit 数: $(git rev-list --count HEAD..origin/main 2>/dev/null || echo '?')"

echo
echo "=== 3) 部署（git pull -> 迁移 -> 重建 -> 重启）==="
sh deploy.sh 2>&1 | tail -18

echo
echo "=== 4) 部署后状态 ==="
git log --oneline -1
docker compose ps --format '{{.Service}}\t{{.Status}}'
docker compose exec -T backend alembic current 2>&1 | tail -1
grep -E '^(APP_BASE_URL|OO_PUBLIC_URL)=' .env | sort -u
curl -s -m 10 http://127.0.0.1:8080/api/health
echo ""
curl -s -m 15 -o /dev/null -w "  ONLYOFFICE 子路径 -> %{http_code} %{content_type}\n" \
  "http://127.0.0.1:8080/onlyoffice/web-apps/apps/api/documents/api.js"
