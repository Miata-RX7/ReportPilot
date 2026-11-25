#!/bin/sh
cd /opt/reportpilot
echo "=== DS 容器日志：回调 / 保存 / 错误 ==="
docker compose logs onlyoffice --tail 500 2>&1 | grep -iE "callback|status|save|error|warn" | tail -25

echo
echo "=== DS 文档服务日志（documentserver 核心）==="
docker compose exec -T onlyoffice sh -c "ls /var/log/onlyoffice/documentserver/ 2>/dev/null | head -6"
docker compose exec -T onlyoffice sh -c "tail -40 /var/log/onlyoffice/documentserver/docservice/out.log 2>/dev/null" | tail -20
