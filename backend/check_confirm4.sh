#!/bin/sh
cd /opt/reportpilot
echo "=== 取最新明文 token 并直接调 _state_of ==="
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -14
import asyncio, json, urllib.request, urllib.error
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.report import Report
from app.api.confirmations import _state_of

async def main():
    async with AsyncSessionLocal() as db:
        r = await db.get(Report, 105)
        print("status =", r.status, " hash前缀 =", (r.confirmation_token or "")[:12])
        # 用后端自己的方式：从库里取不到明文 -> 试常见明文
        for cand in ("S_liK_17GDvLizYZNmWDr8GliqJ_wrKZJUSyQPMmOE",):
            st, msg = _state_of(r, cand)
            print(f"  候选 token -> state={st} msg={msg}")
asyncio.run(main())
PYEOF
echo
echo "=== 从发信日志取最新 token ==="
TOK=$(docker compose logs backend --tail 400 2>&1 | grep -oE "token=S_liK_[A-Za-z0-9_]+" | tail -1 | sed 's/token=//')
echo "最新明文 token = ${TOK:-无}"
if [ -n "$TOK" ]; then
docker compose exec -T -e TOK="$TOK" backend python - <<'PYEOF' 2>&1 | tail -10
import os, json, urllib.request, urllib.error
tok = os.environ["TOK"]
req = urllib.request.Request(f"http://127.0.0.1:8000/api/confirm/105?token={tok}")
try:
    d = json.loads(urllib.request.urlopen(req, timeout=60).read())
    print("token_state =", d.get("token_state"), " message =", d.get("message"))
    print("status      =", d.get("status"))
    nd = d.get("no_data_vars") or []
    print(f"no_data_vars ({len(nd)} 条):")
    for x in nd:
        print(f"   - {x.get('placeholder')} | {x.get('metric_name')}")
except urllib.error.HTTPError as e:
    print("->", e.code, e.read().decode()[:160])
PYEOF
fi
