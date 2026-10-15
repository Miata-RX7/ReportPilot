# ReportPilot auth 版 Smoke Test 执行清单

适用版本：auth 最简版（迁移 0005，JWT 登录 + role 权限）
前置版本：`docs/smoke_test.md`（无 auth 版）已通过，`docs/smoke_test_report.md` 记录了 4 个缺陷修复。

本清单在原有 13 步基础上，重点增加 **CLI 用户管理、JWT 签发与校验、401/403 权限边界、token 过期、迁移回填、日志敏感信息** 六类验证。

统一约定：

- 所有请求带 `-H "Authorization: Bearer $TOKEN"`（PowerShell 用 `$env:TOKEN`）
- token 获取见 §2；admin 与 user 两个角色的 token 都要准备
- `$A` = admin token，`$V` = user（viewer）token

---

## 0. 环境准备（与无 auth 版差异）

`.env` 新增必填项：

```ini
# python -c "import secrets; print(secrets.token_urlsafe(64))"
JWT_SECRET=生成的JWT密钥
JWT_EXPIRE_DAYS=7
```

⚠️ 若 `.env` 是旧版（无 JWT_SECRET），backend 会启动 fail-fast 退出，属预期行为。

依赖有变更（pwdlib → argon2-cffi），**必须重建镜像**：

```bash
docker compose up -d --build backend
docker compose ps                 # backend running / db healthy
curl.exe http://localhost:8000/api/health
```

## 1. 迁移 0005 与 role 回填（重点 6）

```bash
docker compose exec backend alembic upgrade head
# 预期：Running upgrade 0004 -> 0005, user role
```

人工检查点（**关键**：既有行必须被回填，不能是 NULL）：

```bash
docker compose exec db psql -U reportpilot -c "SELECT id, username, role, is_active FROM users ORDER BY id;"
docker compose exec db psql -U reportpilot -c "SELECT count(*) AS null_roles FROM users WHERE role IS NULL;"
```

- [ ] `null_roles = 0`
- [ ] smoke test 期的 id=1 用户 `role = admin`（迁移内的 UPDATE）
- [ ] 其余既有用户为 `user`

## 2. CLI 用户管理与 issue-token（重点 1、2）

> 交互式密码输入不支持管道回显；若必须脚本化，用容器内 `printf` 喂 stdin：
> `docker compose exec -T backend sh -c "printf 'Pass123456\nPass123456\n' | python -m app.cli create-user ..."`

### 2.1 创建 admin 用户

```bash
docker compose exec backend python -m app.cli create-user --username ops --role admin --email ops@example.com
# Password: （至少 8 位）
# Confirm:
```

- [ ] 输出 `用户已创建 id=N username=ops role=admin`，退出码 0

### 2.2 创建 user 角色用户

```bash
docker compose exec backend python -m app.cli create-user --username viewer --role user
```

- [ ] 输出 `role=user`，退出码 0

### 2.3 短密码拒绝

```bash
docker compose exec backend python -m app.cli create-user --username shortpwd --role user
# Password: 1234
```

- [ ] 输出 `密码至少 8 位`，**退出码 1**，未落库

### 2.4 两次输入不一致拒绝

- [ ] 输出 `两次输入不一致`，退出码 1

### 2.5 username 冲突

```bash
docker compose exec backend python -m app.cli create-user --username ops --role admin
```

- [ ] 输出 `用户名已存在: ops`，**退出码 1**

### 2.6 issue-token

```bash
docker compose exec backend python -m app.cli issue-token --username ops --days 7
```

- [ ] 打印一段 JWT（`eyJ` 开头）

```bash
curl.exe http://localhost:8000/api/auth/me -H "Authorization: Bearer $env:TOKEN"
```

- [ ] 200，返回 `{"id":..,"username":"ops","role":"admin",...}`

## 3. 权限三路径（重点 4）

| 场景 | 命令 | 预期 |
|---|---|---|
| 无 token 写操作 | `POST /api/datasources`（不带 header） | **401** `未登录或 token 缺失` |
| user 角色写操作 | `POST /api/datasources` + `$V` | **403** `需要管理员权限` |
| user 角色读操作 | `GET /api/datasources` + `$V` | **200** |

- [ ] 三条状态码与 detail 文案均符合

补充（可选）：`GET /api/reports` 不带 header 也应 401（读操作要求登录的回归点）。

## 4. token 过期（重点 5）

```bash
docker compose exec backend python -m app.cli issue-token --username ops --days 0
curl.exe http://localhost:8000/api/auth/me -H "Authorization: Bearer <该 token>"
```

- [ ] **401** `token 已过期`

边界补充：

- [ ] 篡改 token（末尾加字符）→ 401 `token 无效`
- [ ] 伪造非数字 `sub` 的 token → 401 `token 无效`
- [ ] 用户被停用（`UPDATE users SET is_active=false`）后用旧 token 访问 → 401

## 5. 原 13 步链路重跑（重点 3）

按 `docs/smoke_test.md` 逐步执行，**每步 curl 补 Bearer 头**。重点复核：

- [ ] 步骤 4 `POST /api/datasources`：201，出参只有 `secret_hint`，无明文
- [ ] 步骤 5 `/test`：200 `连接成功`；错误 URL → `URL 不可达: ...`
- [ ] 步骤 6 `POST /api/metrics`：201（注意新增校验：`item_ids` 与 `item_key_pattern` **不能同时为空**，否则 422）
- [ ] 步骤 7 `/compute`：数值与 Zabbix 独立手算**逐位一致**（本期/基线/change_pct）
- [ ] 步骤 8 `POST /api/templates`：201，跨 run 变量识别完整；违规模板 422
- [ ] 步骤 9 `PUT /bindings`：200，`cleared_count` 正确
- [ ] 步骤 10 `preview`：200，下载 docx 中 metric 已替换、insight 为 `[AI摘要待生成]`
- [ ] 步骤 11 `POST /api/reports`：201 `generated`，收件人去重；facts 含证据字段；AI 摘要 `degraded:false`
- [ ] 步骤 12 `submit` → 200 `pending_review`；`approve` → 200 `approved`；audits 逐条增长且 `auditor_id` = 当前 token 用户
- [ ] 步骤 13 `push` → 200 `pushed`，`pushed_at` 非空；重复 push → **409**
- [ ] 下载产物与容器内文件 **md5 一致**
- [ ] 邮件实际收到，附件名 `{模板名}_{period}.docx`

## 6. 日志敏感信息自查（重点 7）

```bash
docker compose logs backend > backend.log 2>&1
```

先触发两类失败登录（作为样本）：

```bash
curl.exe -X POST http://localhost:8000/api/auth/login -H "Content-Type: application/json" -d '{"username":"ops","password":"WrongPass999"}'
curl.exe -X POST http://localhost:8000/api/auth/login -H "Content-Type: application/json" -d '{"username":"ghost","password":"WrongPass999"}'
```

逐项确认：

- [ ] `eyJ` 开头 JWT 原文：**0 命中**
- [ ] 登录密码明文（含失败尝试的密码）：**0 命中**
- [ ] `Authorization` / `Bearer` 字样：**0 命中**
- [ ] Zabbix token 明文/片段：**0 命中**
- [ ] `ENCRYPTION_KEY` / `JWT_SECRET` 值或 `gAAAAAB` 密文头：**0 命中**
- [ ] 登录失败日志**只有 username + IP**，且已知用户与不存在用户**格式完全一致**（不区分失败原因）
- [ ] 两次失败登录的时间间隔可见约 500ms（固定延生效）

样例（合规形态）：

```
WARNING [app.api.auth] 登录失败 username=ops ip=172.19.0.1
WARNING [app.api.auth] 登录失败 username=ghost ip=172.19.0.1
```

---

## 本次执行结果（2026-09-30，真实环境）

环境：Windows 11 + Docker Desktop · PG 16 · 真实 Zabbix（`https://zb.livecom.hk`，
主机组 174 爱迅达办公室 / item 261006、261108）· DeepSeek · exmail SMTP。

| 检查项 | 结果 | 证据 |
|---|---|---|
| 镜像重建 + 启动 | 通过 | `up -d --build backend`，health 200 |
| 迁移 0004→0005 | 通过 | `Running upgrade 0004 -> 0005, user role` |
| role 回填 | 通过 | id=1 → `admin`；`null_roles = 0` |
| CLI 短密码拒绝 | 通过 | `密码至少 8 位`，exit=1 |
| CLI 两次不一致拒绝 | 通过 | `两次输入不一致`，exit=1 |
| CLI 创建 admin / user | 通过 | id=2 `ops`(admin)、id=3 `viewer`(user) |
| CLI username 冲突 | 通过 | `用户名已存在: ops`，exit=1 |
| issue-token → /me | 通过 | 200，`role":"admin"` |
| 无 token 写操作 | 通过 | 401 `未登录或 token 缺失` |
| user 写操作 | 通过 | 403 `需要管理员权限` |
| user 读操作 | 通过 | 200 |
| `--days 0` 过期 token | 通过 | 401 `token 已过期` |
| `/test` 连通性 | 通过 | `{"success":true,"message":"连接成功"}` |
| `/test` 不可达分类 | 通过 | `URL 不可达: Zabbix 地址不可达或超时: ConnectError` |
| 指标试算数值 | 通过 | value `231421.95833333334` / baseline `235963.08389715833` / change_pct `-1.924506786749821`，与独立手算逐位一致 |
| 峰值指标试算 | 通过 | value `276170408.0` / baseline `218156600.0` / change_pct `+26.5927%`，与手算一致 |
| 绑定替换（幂等） | 通过 | 200，`cleared_count=3` |
| 预览 | 通过 | `gi_in→231421.96`、`gi_out→276170408.00`、`summary→[AI摘要待生成]` |
| 报告生成 | 通过 | id=5 `generated`，收件人去重为 1 个 |
| facts/insights | 通过 | 证据字段齐全；AI 真实文本 `degraded:false` |
| submit → approve → push | 通过 | `pending_review` → `approved` → `pushed`，`pushed_at` 非空 |
| 附件一致性 | 通过 | 下载产物 md5 `f0c8c386aad9c1630ccb232e4cbd69ff` 与容器内文件一致 |
| 日志敏感信息 | 通过 | 7 项全 0 命中；失败日志仅 `username + ip`；两次失败间隔 ≈551ms |

### 残留观察项（不阻塞，建议 Phase 2 处理）

1. `insights_json.summary.unverified_numbers = ["0","21"]`：来自指标名
   `gi0_0_21_outbound_peak` 被数字正则切碎，**非 AI 编造**。
   可选优化：校验时先剔除事实 `name`/`object` 中出现的数字片段。
2. 历史遗留：id=1 数据源 `smoke-zabbix-1` 为 `is_active=false`（早期测试软删除），可清理。
3. `JWT_EXPIRE_DAYS` 为环境变量，未在 UI 暴露；轮换 key 需重启服务（Phase 2 可考虑多密钥并存）。

### 本次未改动业务代码

仅新增 `.env` 的 `JWT_SECRET` / `JWT_EXPIRE_DAYS`；临时测试产生的数据源 id=4 与模板 id=2
已软删除后硬删除，未留残留行。
