#!/bin/sh
cd /opt/reportpilot
echo "=== DS docservice 日志：sendServerRequest 上下文 ==="
docker compose exec -T onlyoffice sh -c "grep -B4 -A2 'sendServerRequest returned an error' /var/log/onlyoffice/documentserver/docservice/out.log 2>/dev/null | tail -40"

echo
echo "=== DS 文档服务配置：回调相关 ==="
docker compose exec -T onlyoffice sh -c "grep -iE 'save|callback' /etc/onlyoffice/documentserver/local.json 2>/dev/null | head -10"

echo
echo "=== DS 里这些编辑会话的文件是否还在 cache（能下载吗）==="
docker compose exec -T onlyoffice sh -c "ls -la /var/lib/onlyoffice/documentserver/App_Data/cache/files/data/ 2>/dev/null | grep -E '22_58_edit' | tail -5"

echo
echo "=== 后端侧：编辑器接口返回的 callbackUrl 长什么样 ==="
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -10
from app.services.onlyoffice_service import build_callback_url, build_editor_token
print("  callbackUrl 示例:", build_callback_url(22))
tok = build_editor_token(22, 22, 58, 258, "cb") if hasattr(__import__("app.services.onlyoffice_service", fromlist=["x"]), "build_editor_token") else None
print("  token 示例:", (tok or "")[:60])
PYEOF
