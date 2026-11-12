import asyncio, json, urllib.request, urllib.error, pathlib
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.report import Report
from app.models.task import ReportTask
from app.services.task_service import hash_token

BASE = "http://127.0.0.1:8000"

def call(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req)
        return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)

async def main():
    raw = "test-token-verify-001"
    async with AsyncSessionLocal() as db:
        task = (await db.scalars(select(ReportTask).order_by(ReportTask.id.desc()).limit(1))).first()
        src = (await db.scalars(select(Report).where(Report.file_path.isnot(None)).order_by(Report.id.desc()).limit(1))).first()
        # 造一个待确认的测试报告（复用现成 docx 文件）
        r = Report(
            task_id=task.id, template_id=task.template_id,
            period="2026-10",
            status="pending_confirmation",
            file_path=src.file_path,
            pdf_path=src.pdf_path,
            confirmation_token=hash_token(raw),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=3),
            confirmer_email="verify@test.local",
            recipient_emails=["rc@test.local"],
        )
        db.add(r)
        await db.commit()
        await db.refresh(r)
        rid = r.id
        print("造测试报告 id=%d token=%s" % (rid, raw))

    q = "?token=" + raw
    code, body, hdr = call(f"/api/confirm/{rid}{q}")
    d = json.loads(body)
    print("1) 预览:", code, "| docx_downloaded_at =", d.get("docx_downloaded_at"), "| placeholders =", d.get("summary_placeholders"))

    code, body, _ = call(f"/api/confirm/{rid}/preview.pdf{q}")
    print("2) preview.pdf:", code, len(body), "bytes", body[:5])

    code, body, _ = call(f"/api/confirm/{rid}/download.docx{q}")
    print("3) download.docx:", code, len(body), "bytes", body[:2])

    code, body, _ = call(f"/api/confirm/{rid}/downloaded{q}", "POST")
    print("4) downloaded 上报:", code, body[:40])

    code, body, _ = call(f"/api/confirm/{rid}{q}")
    d = json.loads(body)
    print("5) 预览(再取) docx_downloaded_at =", d.get("docx_downloaded_at"))

    code, body, _ = call(f"/api/confirm/{rid}/confirm-only{q}", "POST", {"token": raw, "summaries": []})
    print("6) confirm-only:", code, body[:160])

    async with AsyncSessionLocal() as db:
        r = await db.get(Report, rid)
        print("7) 状态:", r.status, "| confirmed_no_push_at:", r.confirmed_no_push_at, "| token已清空:", r.confirmation_token is None)
        # 重复点击
        code, body, _ = call(f"/api/confirm/{rid}/confirm-only{q}", "POST", {"token": raw, "summaries": []})
        print("8) 重复 confirm-only:", code, body[:90])
        # 清理
        await db.delete(r)
        await db.commit()
        print("9) 已清理测试报告")
asyncio.run(main())
