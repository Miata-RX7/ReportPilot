# ReportPilot 后端骨架 · 真实 PostgreSQL + 真实 Zabbix 验证报告

日期：2026-09-29 · 环境：Windows + Docker Desktop 29.8.1 / Compose v5.5.1

结论：**骨架立住了**。7 项冒烟全过；接真实 Zabbix 时发现并修复 1 个真实缺陷，最终 `连接成功`（Zabbix 7.2.0）。

---

## 一、本地 PG 环境

### 第 1 步 .env

- `backend/.env` 原本不存在，由 `.env.example` 复制；用项目 `.venv`（全局 python 无 `cryptography`）生成 Fernet key
- `ENCRYPTION_KEY` 长度 44，Fernet 加解密自测 `valid=True`
- 仓库根 `.gitignore` 含 `.env`。**本机无 git CLI，无法跑 `git check-ignore` 复核**；全程未做任何 commit

### 第 2 步 启动 PG

```
docker compose up -d db
pg_isready -U reportpilot -d reportpilot → /var/run/postgresql:5432 - accepting connections
```

0s 即 `healthy`；5432 启动前已确认空闲，**未改端口映射**

### 第 3 步 迁移

```
docker compose build backend
docker compose run --rm backend alembic upgrade head
→ INFO [alembic.runtime.migration] Running upgrade  -> 0001, init
```

### 第 4 步 落地验证（关键）

- `\dt`：7 张业务表（+ `alembic_version` 共 8 行）
- `\d data_sources`：`secret_encrypted` / `secret_hint` / `is_active` 均在
  > 实际表名是 `data_sources`，不是任务书里写的 `datasource`
- `\di`：`"uq_ds_name_active" UNIQUE, btree (name) WHERE is_active = true` ✅

### 第 5 步 冒烟结果

| # | 接口                          | 实际                                           | 结论 |
| - | --------------------------- | -------------------------------------------- | -- |
| 1 | `POST /api/datasources`     | **201**，`secret_hint:"****3456"`，不含明文        | 通过 |
| 2 | `GET /api/datasources`      | 200，脱敏                                       | 通过 |
| 3 | `GET /api/datasources/{id}` | 200，脱敏                                       | 通过 |
| 4 | `POST /{id}/test`（假地址）      | `success=false` / `URL 不可达: ...ConnectError` | 通过 |
| 5 | `PUT /{id}`（无 secret）       | 200；**密文 md5 前后一致 `687dc20a…a746a8`**        | 通过 |
| 6 | `DELETE /{id}`              | **204**，`is_active=f`                        | 通过 |
| 7 | 再次 `POST` 同名                | **201**，新 id=2（`1\|f` 与 `2\|t` 并存）           | 通过 |

反证：id=2 仍 active 时重复 POST 同名 → **409**（证明部分唯一索引是真约束）

> 偏离任务书预期 2 处（非缺陷）：`POST` 返回 **201**；脱敏字段名是 **`secret_hint`** 不是 `secret`。

---

## 二、接真实 Zabbix（<https://zb.livecom.hk/）>

### 发现 1：服务器是自签名证书

```
TLS ERR: SSLCertVerificationError [SSL: CERTIFICATE_VERIFY_FAILED]
         certificate verify failed: self-signed certificate
```

`verify_ssl=false` 时网络完全正常 → 不是网络/代理问题。**当前按约定用 `verify_ssl=false`**；若要恢复强校验，需把该站点 CA 挂进容器。

### 发现 2：adapter 真实缺陷（已修复）

`_call()` 无条件附加 `Authorization: Bearer`，而 Zabbix 7.x 的 `apiinfo.version` **禁止**带认证头：

```
不带 Authorization → 200 {"result":"7.2.0"}                      ← 成功
带 Authorization   → 200 {"error":{"code":-32602,
                     "data":"The \"apiinfo.version\" method must be
                     called without authorization header."}}
```

后果：`test_connection()` 第一步在真实 Zabbix 上**永远失败**，`/test` 不可能返回 `success=true`（即使 token 有效）。

**修复**（`app/services/adapters/zabbix_adapter.py`）：

- `_call()` 增加 `*, auth: bool = True`；`auth=False` 时不附加 `Authorization` 头
- 保留 `if not self._secret: raise AdapterAuthError` 前置校验（不破坏 `test_missing_token`）
- `test_connection()` 改为 `self._call("apiinfo.version", {}, auth=False)`

同步修正测试 `tests/test_zabbix_adapter.py::TestConnection::test_success`——原断言"首次调用也携带认证头"正是该 bug 的固化，改为断言第 1 次无认证头、第 2 次有。

**单元测试：36 passed**（容器 `python:3.11` 内跑）

### 真实 token 验证结果

```
PUT /api/datasources/2  → 200，secret_hint "****3a91"
POST /api/datasources/2/test → {"success":true,"message":"连接成功"}
日志：Zabbix 连通性测试通过，版本 7.2.0          （09:20:32、09:22:02、09:23:18 多次复现）
```

安全核验：

- 日志中**无明文 token**（已 grep 确认）
- DB 只存密文：`id=2 | ****3a91 | gAAAAABqu4LQ…`

---

## 三、附带修复：dev 热重载

我在容器内跑 pytest 生成了 root 属主的 `/code/.pytest_cache`，导致 uvicorn `--reload` 的 watchfiles 崩溃：

```
WatchfilesRustInternalError: IO error ... /code/.pytest_cache: Permission denied
```

这也是最初改代码未自动生效的原因之一。已删除该目录并重启：

```
INFO: Started reloader process [1] using WatchFiles
WatchFiles detected changes in 'app/config.py'. Reloading...   ← 热重载已恢复
```

**后续注意**：改代码后 uvicorn 会自动重载（已验证）；但**不要在容器内跑 pytest**（会再造 `.pytest_cache`），用 `python -m pytest -p no:cacheprovider` 或在宿主机跑。

---

## 四、约束遵守

- volume `backend_pgdata` **未删除**，数据保留（id=1 软删除、id=2 真实 Zabbix 数据源）
- `docker-compose.yml` 端口映射**未改动**
- `.env` 未提交；未执行任何 git 写操作（本机无 git CLI）
- 未遇 Windows 权限/WSL 阻塞问题

## 五、结论

骨架在真实 PG 16 上立住，并已接通真实 Zabbix 7.2.0。冒烟 7/7 通过 + 反证 1 项通过，发现并修复 1 个会导致 `/test` 永久失败的真实缺陷，单元测试 36 全绿，安全约束（脱敏出参、日志与 DB 无明文）均达标。
