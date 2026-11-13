"""普通用户（AD 账号 337）端到端可用性测试（临时脚本，测完删除）。

覆盖：登录 -> 拿 token -> 逐个调普通用户会用到的接口 -> 检查状态码与权限边界。
"""

import asyncio
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
USER = "337"
PWD = "Livec0m@2026"


def call(path, token=None, method="GET", body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    try:
        r = urllib.request.urlopen(req)
        return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:200]


def show(label, code, data, expect=None, note=""):
    ok = "OK " if (expect is None or code == expect) else "!! "
    detail = ""
    if isinstance(data, list):
        detail = f"{len(data)} 条"
    elif isinstance(data, dict):
        if "detail" in data:
            detail = str(data["detail"])[:70]
        else:
            detail = ", ".join(list(data.keys())[:5])
    else:
        detail = str(data)[:60]
    print(f"{ok} {label:38} HTTP {code:3}  {detail}  {note}")


async def main():
    print("=" * 78)
    print("一、登录（真实 AD 认证）")
    print("=" * 78)
    code, d = call("/api/auth/login", method="POST", body={"username": USER, "password": PWD})
    if code != 200:
        print(f"!! 登录失败 HTTP {code}: {d}")
        return
    token = d["access_token"]
    u = d["user"]
    print(f"OK  登录成功  id={u['id']} username={u['username']} "
          f"display={u['display_name']} email={u['email']}")
    print(f"    source={u.get('source')} role={u['role']} is_active={u['is_active']}")

    print()
    print("=" * 78)
    print("二、当前用户 & 只读接口（普通用户日常会用到的）")
    print("=" * 78)
    code, d = call("/api/auth/me", token)
    show("GET /auth/me", code, d, 200)

    code, d = call("/api/datasources", token)
    show("GET /datasources", code, d, 200, "(数据源全局共享)")

    code, d = call("/api/metrics", token)
    show("GET /metrics", code, d, 200, "(按创建人隔离：自己的)")

    code, d = call("/api/templates", token)
    show("GET /templates", code, d, 200, "(自己的 + 被授权的)")

    code, d = call("/api/report-tasks", token)
    show("GET /report-tasks", token and code, d, 200, "(按创建人隔离)")

    code, d = call("/api/reports", token)
    show("GET /reports", code, d, 200)

    code, d = call("/api/system/settings", token)
    show("GET /system/settings", code, d, 200, "(登录即可读)")

    code, d = call("/api/users", token)
    show("GET /users", code, d, 200, "(默认只返回启用用户)")

    code, d = call("/api/push-records", token)
    show("GET /push-records", code, d, 200)

    print()
    print("=" * 78)
    print("三、权限边界（普通用户**不该**能做的事 -> 应 403/404）")
    print("=" * 78)
    code, d = call("/api/system/settings/ad", token)
    show("GET /system/settings/ad", code, d, 403, "(AD 配置是 admin-only)")

    code, d = call("/api/system/settings/ad/test", token, "POST")
    show("POST /system/settings/ad/test", code, d, 403)

    code, d = call("/api/system/settings/ad/sync", token, "POST")
    show("POST /system/settings/ad/sync", code, d, 403)

    code, d = call("/api/users", token, "POST",
                   {"username": "hacker", "email": "h@h.com", "password": "x",
                    "role": "admin"})
    show("POST /users (新建 admin)", code, d, 403, "(不能建管理员)")

    code, d = call("/api/system/settings/mail", token, "PUT", {})
    show("PUT /system/settings/mail", code, d, 403)

    print()
    print("=" * 78)
    print("四、普通用户**应该**能做的事")
    print("=" * 78)
    # 建一个指标（所有人可创建）
    code, d = call("/api/metrics", token, "POST", {
        "name": "__verify_user_metric__",
        "data_source_id": 8,
        "metric_type": "scalar",
        "unit": "%",
        "query_config": {"host_group_ids": ["135"], "host_ids": [], "item_ids": ["189066"]},
        "type_config": {"transform": {"mul": 1.0, "add": 0.0}},
        "calc_formula": {"agg": "avg", "compare": "none", "baseline_lookback": None},
    })
    show("POST /metrics (建自己的指标)", code, d, 201)
    mid = d.get("id") if code == 201 else None

    if mid:
        code, d = call(f"/api/metrics/{mid}", token)
        show("GET /metrics/{id} (自己的)", code, d, 200)
        code, d = call(f"/api/metrics/{mid}", token, "PUT",
                       {"display_precision": 3})
        show("PUT /metrics/{id} (改自己的)", code, d, 200)
        code, d = call(f"/api/metrics/{mid}/compute", token, "POST",
                       {"period": "2026-10"})
        show("POST /metrics/{id}/compute (试算)", code, d, 200)
        code, d = call(f"/api/metrics/{mid}", token, "DELETE")
        show("DELETE /metrics/{id} (删自己的)", code, d, 204)

    # 建一个报告任务
    code, d = call("/api/templates", token)
    tpl = d[0] if isinstance(d, list) and d else None
    if tpl:
        code, d = call("/api/report-tasks", token, "POST", {
            "name": "__verify_user_task__",
            "template_id": tpl["id"],
            "cron_expr": "0 9 1 * *",
            "confirmer_email": "zhou.keyao@livecom.hk",
            "recipient_emails": ["rc@livecom.hk"],
            "period_granularity": "month",
            "period_offset": -1,
        })
        show("POST /report-tasks (建自己的任务)", code, d, 201)
        tid = d.get("id") if code == 201 else None
        if tid:
            code, d = call(f"/api/report-tasks/{tid}", token)
            show("GET /report-tasks/{id}", code, d, 200)
            code, d = call(f"/api/report-tasks/{tid}", token, "DELETE")
            show("DELETE /report-tasks/{id}", code, d, 204)
    else:
        print("!! 没有可用模板，跳过任务测试")

    print()
    print("=" * 78)
    print("五、能不能改**别人**的东西")
    print("=" * 78)
    # ops 的指标（存量 33 个归 ops）
    code, d = call("/api/metrics", token)
    print(f"    337 名下指标数: {len(d) if isinstance(d, list) else '?'}")
    code, d = call("/api/metrics?include_inactive=true", token)
    print(f"    含停用: {len(d) if isinstance(d, list) else '?'} 条")
    # 试改一个 ops 的指标
    code, d = call("/api/metrics/50", token, "PUT", {"display_precision": 2})
    show("PUT /metrics/50 (ops 的指标)", code, d, 404, "(看不到 -> 404)")
    code, d = call("/api/report-tasks/17", token, "PUT", {"name": "hacked"})
    show("PUT /report-tasks/17 (ops 的任务)", code, d, 404)

    print()
    print("=" * 78)
    print("六、清理")
    print("=" * 78)
    code, d = call("/api/metrics", token)
    for m in (d if isinstance(d, list) else []):
        if str(m.get("name", "")).startswith("__verify"):
            c2, _ = call(f"/api/metrics/{m['id']}", token, "DELETE")
            print(f"    清理指标 {m['name']} -> {c2}")
    code, d = call("/api/report-tasks", token)
    for t in (d if isinstance(d, list) else []):
        if str(t.get("name", "")).startswith("__verify"):
            c2, _ = call(f"/api/report-tasks/{t['id']}", token, "DELETE")
            print(f"    清理任务 {t['name']} -> {c2}")
    print("    完成")


asyncio.run(main())
