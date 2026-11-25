#!/bin/sh
# ReportPilot 开发测试环境部署（2026-11-25 起改用 git 拉代码）
#
# 用法（在服务器上）：sh /opt/reportpilot/deploy.sh
# 流程：git pull -> docker compose build -> up -d -> 健康检查
#
# 与旧的 scp 方式相比：
#   - 代码版本由 git 统一管理，服务器不再有「手工同步的文件」
#   - .env / deploy/ 等运行时文件由 .gitignore 排除，不会被 pull 覆盖
set -e
cd /opt/reportpilot

BRANCH="${BRANCH:-main}"

echo "=== 1) 拉代码 ==="
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
echo "  HEAD -> $(git rev-parse --short HEAD)  $(git log -1 --format=%s)"

echo
echo "=== 2) 迁移（如果代码里有新迁移）==="
docker compose build backend 2>&1 | tail -1
docker compose up -d backend 2>&1 | tail -1
sleep 16
docker compose exec -T backend alembic upgrade head 2>&1 | grep -iE "upgrade|error" | tail -3 || true
docker compose exec -T backend alembic current 2>&1 | tail -1

echo
echo "=== 3) 重建并重启 ==="
docker compose build backend frontend 2>&1 | tail -1
docker compose up -d --force-recreate backend frontend 2>&1 | tail -2
sleep 20

echo
echo "=== 4) 状态 ==="
docker compose ps --format '{{.Service}}\t{{.Status}}'
echo -n "  健康检查: "
curl -s -m 10 http://127.0.0.1:8080/api/health || echo "失败"
echo ""
echo -n "  ONLYOFFICE 子路径: "
curl -s -m 15 -o /dev/null -w "%{http_code}\n" \
  "http://127.0.0.1:8080/onlyoffice/web-apps/apps/api/documents/api.js"

echo
echo "=== 5) 工作区（应为 0 差异）==="
git status --short | head -5
echo "  差异项: $(git status --short | wc -l)"
