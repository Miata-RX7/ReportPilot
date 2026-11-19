#!/bin/sh
cd /opt/reportpilot
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -12
import json, urllib.request, urllib.error

# 用户截图里的真实确认链接（明文 token）
TOKEN = "S_liK_17GDvLizYZNmWDr8GliqJ_wrKZJUSyQPMmOE"
RID = 105

req = urllib.request.Request(
    f"http://127.0.0.1:8000/api/confirm/{RID}?token={TOKEN}")
try:
    d = json.loads(urllib.request.urlopen(req, timeout=60).read())
    print("token_state =", d.get("token_state"), " message =", d.get("message"))
    print("status      =", d.get("status"))
    nd = d.get("no_data_vars") or []
    print(f"no_data_vars ({len(nd)} 条):")
    for x in nd:
        print(f"   - {x.get('placeholder')} | {x.get('metric_name')} | {str(x.get('reason'))[:50]}")
except urllib.error.HTTPError as e:
    print("preview ->", e.code, e.read().decode()[:200])
PYEOF
