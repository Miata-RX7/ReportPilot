#!/bin/sh
cd /opt/reportpilot
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -16
import asyncio, json, urllib.request, urllib.error
from app.database import AsyncSessionLocal
from app.models.report import Report
from app.services.task_service import hash_token

RAW = "TESTTOKEN_partial_success_123"

async def main():
    async with AsyncSessionLocal() as db:
        r = await db.get(Report, 105)
        r.confirmation_token = hash_token(RAW)   # 换成已知明文的 hash
        await db.commit()
        print("已为报告 105 设置测试 token，status =", r.status)

    req = urllib.request.Request(
        f"http://127.0.0.1:8000/api/confirm/105?token={RAW}")
    d = json.loads(urllib.request.urlopen(req, timeout=60).read())
    print("token_state =", d.get("token_state"), " message =", d.get("message"))
    print("status      =", d.get("status"))
    print("metric_count=", d.get("metric_count"))
    nd = d.get("no_data_vars") or []
    print(f"no_data_vars ({len(nd)} 条):")
    for x in nd:
        print(f"   - {x.get('placeholder')} | {x.get('metric_name')} | {str(x.get('reason'))[:46]}")

    # 预览 PDF 应该也能下（确认人能拿到半成品）
    req2 = urllib.request.Request(
        f"http://127.0.0.1:8000/api/confirm/105/preview.pdf?token={RAW}")
    try:
        rr = urllib.request.urlopen(req2, timeout=120)
        body = rr.read()
        print(f"preview.pdf -> {rr.status} {len(body)} 字节 {'PDF' if body[:4] == b'%PDF' else ''}")
    except urllib.error.HTTPError as e:
        print("preview.pdf ->", e.code, e.read().decode()[:120])
asyncio.run(main())
PYEOF
