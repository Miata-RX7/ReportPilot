"""逐个试算模板 19 当前版本绑定的所有指标（周期 2026-10），找出取不到数的。"""

import asyncio
import json
import urllib.error
import urllib.request

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.template import TemplateBinding, TemplateVersion, WordTemplate
from app.models.user import User
from app.services.security import create_token

BASE = "http://127.0.0.1:8000"
PERIOD = "2026-10"


async def api_call(path, token, method="GET", body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers, method=method,
    )
    try:
        r = urllib.request.urlopen(req, timeout=120)
        return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:160]
    except Exception as e:
        return 0, f"{type(e).__name__}: {str(e)[:80]}"


async def main() -> None:
    async with AsyncSessionLocal() as db:
        u = (await db.scalars(select(User).where(User.username == "337"))).first()
        token = create_token(u.id, u.username, u.role)
        tpl = await db.get(WordTemplate, 19)
        ver = await db.get(TemplateVersion, tpl.current_version_id)
        rows = (
            await db.scalars(
                select(TemplateBinding).where(TemplateBinding.version_id == ver.id)
            )
        ).all()
        from app.models.metric import MetricDefinition

        metas = []
        for b in rows:
            m = await db.get(MetricDefinition, b.binding_ref_id)
            metas.append((b.placeholder, m))

    ok, fail = [], []
    for ph, m in metas:
        code, d = await api_call(f"/api/metrics/{m.id}/compute", token, "POST", {"period": PERIOD})
        if code == 200:
            ok.append((ph, m.name, m.metric_type))
        else:
            fail.append((ph, m.name, m.metric_type, str(d)[:90]))

    print(f"周期 {PERIOD}：成功 {len(ok)} 个，失败 {len(fail)} 个\n")
    if fail:
        print("=== 取不到数的（就是报告生成失败的元凶）===")
        for ph, name, mt, err in fail:
            print(f"  {ph:38} {name:34} {mt:10} {err}")
    print("\n=== 成功的（前 5 个示例）===")
    for ph, name, mt in ok[:5]:
        print(f"  {ph:38} {name:34} {mt}")


asyncio.run(main())
