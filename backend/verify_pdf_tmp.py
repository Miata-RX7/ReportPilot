import asyncio, json, urllib.request, urllib.error, hashlib
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.report import Report

BASE = "http://127.0.0.1:8000"

def call(path, method="GET", body=None, token=None):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

async def main():
    async with AsyncSessionLocal() as db:
        # 找一个待确认的报告
        r = (await db.scalars(select(Report).where(Report.status == "pending_confirmation").order_by(Report.id.desc()).limit(1))).first()
        if r is None:
            print("没有 pending_confirmation 的报告，测已推送的那份的只读接口")
            r = (await db.scalars(select(Report).where(Report.status == "pushed").order_by(Report.id.desc()).limit(1))).first()
        rid = r.id
        print("report", rid, "status", r.status, "token?", bool(r.confirmation_token))
        raw_token = None
        if r.confirmation_token:
            # 库里存的是 sha256，无法还原原始 token -> 用它做不了免登录接口
            print("（token 只存哈希，无法在脚本里构造；改用接口注册的新报告验证）")
        # PDF 文件本身
        import pathlib
        if r.pdf_path:
            p = pathlib.Path(r.pdf_path)
            print("PDF:", p.name, p.stat().st_size, "bytes, magic:", p.read_bytes()[:5])
        else:
            print("PDF: 无")
asyncio.run(main())
