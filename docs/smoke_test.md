# ReportPilot 端到端 Smoke Test（Phase 1 手动验证）

目的：在真实环境（PostgreSQL + 真实 Zabbix + 真实 SMTP + 真实 AI 接口）下，
走通"数据源 → 指标 → 模板 → 预览 → 报告 → 审核 → 推送"完整链路。

前置条件：

- Docker / docker compose 可用
- 一个可访问的 Zabbix 7.2 实例 + API token（有 hostgroup.get / item.get / trend.get 权限）
- 一组可用的 SMTP 账号（或可匿名发送的中继）
- 一个 OpenAI 兼容协议的 AI 端点（可选，不配置则验证降级路径）
- 本文档统一用 `curl`（Windows PowerShell 请用 `curl.exe`，避免别名到 Invoke-WebRequest）

## 0. 环境准备

```bash
cd backend
cp .env.example .env
```

编辑 `.env`，**必填**：

```ini
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=上一步生成的密钥
# python -c "import secrets; print(secrets.token_urlsafe(64))"
JWT_SECRET=生成的JWT密钥
DATABASE_URL=postgresql+asyncpg://reportpilot:reportpilot@db:5432/reportpilot

# AI（可选；留空则摘要走降级占位，不影响流程）
AI_BASE_URL=https://your-ai-endpoint/v1
AI_API_KEY=sk-...
AI_MODEL=gpt-4o-mini

# SMTP
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USERNAME=reportpilot@example.com
SMTP_PASSWORD=your-password
SMTP_FROM=reportpilot@example.com
SMTP_USE_TLS=true
```

注意：`DATABASE_URL` 在 compose 里已被 backend 服务的环境变量覆盖为
`postgresql+asyncpg://reportpilot:reportpilot@db:5432/reportpilot`，
host 必须是 `db`（容器网络名），不是 localhost。

## 1. 启动服务

```bash
docker compose up -d db backend
docker compose ps            # db 应为 healthy，backend 为 running
curl.exe http://localhost:8000/api/health
```

预期返回：`{"status":"ok"}`

常见错误：

- backend 反复重启 → `docker compose logs backend` 看是否
  `ENCRYPTION_KEY 未配置`（启动 fail-fast，属预期行为，补 key 后重启）
- db 端口冲突 → 改 compose 里 `5432:5432` 的宿主机端口

## 2. 数据库迁移（真实 PG）

```bash
docker compose exec backend alembic upgrade head
```

预期输出：`INFO  [alembic.runtime.migration] Running upgrade  -> 0001, ... 0004`

人工检查点：

```bash
docker compose exec db psql -U reportpilot -c "\dt"
```

应看到 7 张表：users / data_sources / metric_definitions / word_templates /
template_bindings / reports / report_audits。

## 3. 创建管理员用户并获取 Token

```bash
docker compose exec backend python -m app.cli create-user \
  --username admin --role admin --email admin@example.com
# Password: （交互输入，至少 8 位，不进 shell history）
# Confirm:
```

获取 token（后续所有请求都要带）：

```bash
curl.exe -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "你的密码"}'
```

预期返回：`{"access_token": "eyJ...", "token_type": "bearer", "user": {...}}`

把 token 存到环境变量（后续命令引用）：

```powershell
$env:TOKEN = "上一步返回的 access_token"
# Linux/Mac: export TOKEN=...
```

验证 token 有效：

```bash
curl.exe http://localhost:8000/api/auth/me -H "Authorization: Bearer $env:TOKEN"
```

预期返回当前用户信息（含 `"role": "admin"`）。

**注意**：从本步开始，所有 curl 命令都必须带
`-H "Authorization: Bearer $env:TOKEN"`（无 token 一律 401；
非 admin 调数据源/指标/模板写操作 403）。

## 4. 创建 Zabbix 数据源

```bash
curl.exe -X POST http://localhost:8000/api/datasources \
  -H "Authorization: Bearer $env:TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "生产 Zabbix",
    "type": "zabbix",
    "config": {
      "url": "http://zabbix.example.com",
      "auth_type": "bearer",
      "timeout": 30,
      "verify_ssl": false
    },
    "secret": "你的Zabbix-API-token"
  }'
```

预期返回（201）：

```json
{
  "id": 1,
  "name": "生产 Zabbix",
  "type": "zabbix",
  "config": {"url": "http://zabbix.example.com", "auth_type": "bearer", "timeout": 30, "verify_ssl": false},
  "is_active": true,
  "secret_hint": "****末尾4位",
  "created_at": "..."
}
```

人工检查点：

- 返回中**只有** `secret_hint`，不出现 token 明文
- `docker compose exec db psql -U reportpilot -c "SELECT secret_hint, length(secret_encrypted) FROM data_sources;"`
  secret_encrypted 是长 Base64 串，不是明文

常见错误：409 = 名称重复；422 = 字段校验失败（看 detail）。

## 5. 连通性测试（验证三类错误可区分）

正常：

```bash
curl.exe -X POST http://localhost:8000/api/datasources/1/test \
  -H "Authorization: Bearer $env:TOKEN"
```

预期：`{"success":true,"message":"连接成功"}`

错误区分验证（逐个构造，观察 message 前缀）：

| 构造方法 | 预期 message 前缀 |
|---|---|
| `config.url` 改为不可达地址后 PUT 更新 | `URL 不可达: ...` |
| `secret` 改成一个错误 token 后 PUT 更新 | `认证失败: ...` |
| 用一个只有 apiinfo 权限、无 hostgroup 权限的 token | `权限不足: ...` |

改回正确配置的方法：

```bash
curl.exe -X PUT http://localhost:8000/api/datasources/1 \
  -H "Authorization: Bearer $env:TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"config": {"url": "http://zabbix.example.com", "auth_type": "bearer", "timeout": 30, "verify_ssl": false}, "secret": "正确token"}'
```

人工检查点：三类错误的 message **均不含 token 明文**。

## 6. 创建指标

先在 Zabbix 前端确认一个主机组 ID（如 4 = Linux servers）和一个数值型
item key（如 `system.cpu.util`）。

```bash
curl.exe -X POST http://localhost:8000/api/metrics \
  -H "Authorization: Bearer $env:TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "cpu_usage_avg",
    "data_source_id": 1,
    "query_config": {
      "host_group_ids": ["4"],
      "item_key_pattern": "system.cpu.util"
    },
    "calc_formula": {"agg": "avg", "compare": "mom", "baseline_lookback": 1},
    "unit": "%",
    "period_type": "monthly",
    "display_precision": 2
  }'
```

预期返回（201）：含 id / query_config / calc_formula / is_active=true。

常见错误：422 `数据源不存在或已停用`；409 名称重复；
422 `compare 非 none 时 baseline_lookback 必填`。

## 7. 试算并人工核对数值（关键检查点）

```bash
curl.exe -X POST http://localhost:8000/api/metrics/1/compute \
  -H "Authorization: Bearer $env:TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"period": "2026-08"}'
```

预期返回（200）：

```json
{
  "metric_id": 1,
  "name": "cpu_usage_avg",
  "value": 31.225,
  "baseline_value": 30.8,
  "change_pct": 1.38,
  "unit": "%",
  "source": "生产 Zabbix",
  "object": "Linux servers（N 台主机, M 个监控项）",
  "formula": "avg(trend.hourly.value_avg) → avg over M items [key~'system.cpu.util'] → mom(1 calendar month)",
  "period": "2026-08"
}
```

**人工核对（必须做）**：

1. 登录 Zabbix 前端 → Monitoring → Latest data / Graphs，选同一主机组同一 item，
   切到该月的 trend（小时聚合）视图；
2. 抽样 2~3 个小时点，核对 trend 的 avg/min/max 与原始数据一致（确认取数区间正确）；
3. 口径核对：`value` 应等于「每个 item 当月 hourly value_avg 的平均，再跨 item 平均」。
   可用以下 SQL 在 Zabbix 库直接验算（MySQL 示例，PG 语法类似）：

   ```sql
   SELECT itemid, AVG(value_avg)
   FROM trends
   WHERE itemid IN (你的item列表)
     AND clock >= UNIX_TIMESTAMP('2026-08-01')
     AND clock <  UNIX_TIMESTAMP('2026-09-01')
   GROUP BY itemid;
   -- 再对结果集求平均，应与 API 返回的 value 一致
   ```

4. `baseline_value` 用上一个月同口径验算；
5. `change_pct = (value - baseline) / baseline * 100` 手算复核。

常见错误：

- 422 `本期时间区间内无趋势数据` → 该月 Zabbix 无 trend（housekeeper 清理周期 /
  item 无数据），换一个有数据的月份
- 422 `以下 item 未在指定主机组中找到` → item_ids 与 host_group_ids 不匹配
- 502 `URL 不可达/认证失败` → 回到第 5 步排查

## 8. 上传真实 Word 模板

准备一个 docx，正文只含简单变量（复杂语法会被 422 拒绝），例如：

```
本月 CPU 平均使用率：{{ cpu_usage }} %
{{ summary }}
```

```bash
curl.exe -X POST http://localhost:8000/api/templates \
  -H "Authorization: Bearer $env:TOKEN" \
  -F "name=运维月报模板" \
  -F "file=@C:/path/to/template.docx"
```

预期返回（201）：`placeholders: ["cpu_usage", "summary"]`，`unbound` 同上。

人工检查点：

- 变量名识别完整（Word 拆 run 不影响解析）
- 用含 `{% if %}` 或 `{{ a.b }}` 的模板再传一次，应 422 且 detail.violations
  列出违规片段

## 9. 绑定变量

```bash
curl.exe -X PUT http://localhost:8000/api/templates/1/bindings \
  -H "Authorization: Bearer $env:TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "bindings": [
      {"placeholder": "cpu_usage", "binding_type": "metric", "binding_ref_id": 1},
      {"placeholder": "summary", "binding_type": "insight"}
    ]
  }'
```

预期：`cleared_count: 0`，两条绑定；insight 的 `binding_key` 自动为 `"summary"`。

人工检查点：`GET /api/templates/1` 确认 `unbound: []`。

## 10. 预览

```bash
curl.exe -X POST http://localhost:8000/api/templates/1/preview \
  -H "Authorization: Bearer $env:TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"period": "2026-08"}' \
  --output preview.docx
```

人工检查点：用 Word 打开 preview.docx：

- `{{ cpu_usage }}` 被替换为第 7 步的 value（两位小数，因 display_precision=2）
- `{{ summary }}` 为 `[AI摘要待生成]`（preview 不调 AI，属预期）
- 原模板文件未被修改

## 11. 生成报告

```bash
curl.exe -X POST http://localhost:8000/api/reports \
  -H "Authorization: Bearer $env:TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": 1,
    "period": "2026-08",
    "recipient_emails": ["your-real-email@example.com"]
  }'
```

预期返回（201）：`status: "generated"`，`recipient_emails` 已去重。

人工检查点：

- `GET /api/reports/1` 查看：
  - `facts_json[0]` 含 value/unit/source/object/formula/period 完整证据字段
  - `insights_json.summary.text` 为真实 AI 文本（配置了 AI）；
    `degraded: false`；`unverified_numbers` 应为空或仅少量（有内容时人工
    判断 AI 是否编造了数字）
  - 未配置 AI 时 `degraded: true`、text 为占位文本（验证降级路径）
- AI 文本 ≤ 200 字，且引用的数字与 facts_json 中一致

常见错误：422 生成失败（报告回退 draft，detail 里看 `last_error` +
`error_stage: "generate"`）。

## 12. 审核流转

```bash
curl.exe -X POST http://localhost:8000/api/reports/1/submit \
  -H "Authorization: Bearer $env:TOKEN"
curl.exe -X POST http://localhost:8000/api/reports/1/approve \
  -H "Authorization: Bearer $env:TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"comment": "数值已核对"}'
```

预期状态依次变为 `pending_review` → `approved`。

人工检查点：`GET /api/reports/1` 的 audits 数组逐条增长
（generate → submit → approve），auditor_id=1。

顺带验证非法流转：`generated` 状态直接 approve 应 409。

## 13. 推送与收件核对（关键检查点）

```bash
curl.exe -X POST http://localhost:8000/api/reports/1/push \
  -H "Authorization: Bearer $env:TOKEN"
```

预期：`status: "pushed"`，`pushed_at` 非空。

失败时：`status: "push_failed"`，`last_error` / `error_stage: "push"` 有值；
修复 SMTP 后 `POST /api/reports/1/retry-push` 重试。

**人工核对（必须做）**：

1. 登录收件邮箱，确认收到邮件：
   - 主题 `【ReportPilot】运维月报模板 2026-08 月报`
   - 附件名 `运维月报模板_2026-08.docx`
2. 下载附件，与接口产物比对一致：

   ```bash
   curl.exe http://localhost:8000/api/reports/1/download \
     -H "Authorization: Bearer $env:TOKEN" \
     --output report.docx
   ```

   打开 report.docx 确认：`cpu_usage` 为计算值、`summary` 为**真实 AI 文本**
   （不是占位符），与邮件附件内容一致。

3. 重复推送验证幂等：再调一次 `/push` 应返回 409。

## 附：容器日志自查清单（敏感信息泄露）

推送完成后，导出全部日志检查：

```bash
docker compose logs backend > backend.log 2>&1
docker compose logs db > db.log 2>&1
```

逐项确认（可用 findstr / grep）：

- [ ] `backend.log` 中**不存在** Zabbix API token 明文
      （搜索 token 原文及其末 4 位以外的任意子串）
- [ ] `backend.log` 中**不存在** `Authorization` / `Bearer` 字样
- [ ] `backend.log` 中**不存在** JWT 原文（`eyJ` 开头的 token 串）
- [ ] `backend.log` 中**不存在**登录密码（登录日志只有 username + IP）
- [ ] `backend.log` 中**不存在** SMTP_PASSWORD 的值
- [ ] `backend.log` 中**不存在** ENCRYPTION_KEY 的值
- [ ] `backend.log` 中**不存在** AI_API_KEY 的值
- [ ] 数据源连通性失败时，日志只有方法名/状态码/异常类型，无请求头与 body
- [ ] `db.log` 中 SQL 语句不携带 secret_encrypted 明文以外的敏感值
- [ ] 关键操作日志齐全：数据源创建/更新、指标计算完成、报告生成各阶段、
      状态流转、邮件发送（便于追溯）

任一项打不上勾，先不要交付，回到对应模块检查日志点。
