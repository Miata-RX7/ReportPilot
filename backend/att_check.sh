#!/bin/sh
cd /opt/reportpilot
docker compose build backend 2>&1 | tail -1
docker compose up -d --force-recreate backend 2>&1 | tail -1
sleep 18
echo "=== 验证：构造一封带中文附件名的邮件，看 MIME 里的 filename ==="
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -14
import asyncio
from email.message import EmailMessage
from app.database import AsyncSessionLocal
from app.services import report_summary_service
from pathlib import Path

async def main():
    async with AsyncSessionLocal() as db:
        files = await report_summary_service.list_files(db, 107)
        for f in files:
            p = Path(f.stored_path)
            print("原始文件名:", f.filename, "| 磁盘名:", p.name, "| 存在:", p.exists())
            if not p.exists():
                continue
            msg = EmailMessage()
            msg["Subject"] = "t"
            msg.add_attachment(
                p.read_bytes(), maintype="application", subtype="octet-stream",
                filename=f.filename or p.name,
            )
            raw = msg.as_bytes()
            # 打印 Content-Disposition 那几行
            for line in raw.decode("utf-8", "replace").splitlines():
                if "filename" in line.lower() or "Content-Type" in line:
                    print("   MIME:", line[:150])
asyncio.run(main())
PYEOF
