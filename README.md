# ReportPilot

> 数据接入 → 数据汇算 → AI 洞察摘要 → 模板生成 → 审核推送
>
> **AI 只负责写文字，所有指标数值由汇算引擎独立产出**，两者在数据层强制分离。

把「监控指标」变成「可审核、可追溯的 Word 月报」：从 Zabbix 取数、按周期汇算、用 AI 生成分析文字、
套进 Word 模板、邮件推送并等待确认。

---

## 目录

- [它能做什么](#它能做什么)
- [数据是怎么流动的](#数据是怎么流动的)
- [技术栈](#技术栈)
- [安装与运行](#安装与运行)
- [配置项](#配置项)
- [创建账号](#创建账号)
- [ONLYOFFICE：模板在线编辑（可选）](#onlyoffice模板在线编辑可选)
- [验证安装是否成功](#验证安装是否成功)
- [开发与测试](#开发与测试)
- [安全须知](#安全须知)
- [延伸文档](#延伸文档)

---

## 它能做什么

### 1. 多源数据接入

- **Zabbix 7.x** 为主（JSON-RPC over HTTP）：主机组 → 主机 → 监控项三级浏览后直接选进指标
- 数据源凭据用 **Fernet 对称加密**落库，接口与日志**永不回显明文**，只给 `secret_hint`（`****末4位`）
- 每个数据源可独立配置超时、限流、CA 证书校验（`verify_ssl`）
- adapter 层抽象清晰，可扩展到其它监控源

### 2. 指标与取数汇算（五类指标）

| 类型 | 用途 | 取数 | 产出 |
| --- | --- | --- | --- |
| `scalar` | 数值 | 多 item 聚合（`avg`/`sum`/`max`/`min`…） | 浮点值 + 环比 + 基线 |
| `text` | 文本 | 0~1 个 item 的最新值，或固定文案 | 字符串 |
| `curve` | 折线图 | 每 item 一条曲线，**整段合并成一次 `trend.get`** | 序列点集 |
| `pie` | 饼图 | 单 item，按 UP/DOWN 值域统计小时占比 | UP/DOWN 计数与占比 |
| `time` | 时间 | **不取数**，按计算时刻 + 偏移 + 格式词表渲染 | 字符串 |

- **两阶段聚合**：先按小时聚合区间内的 trend，再跨 item 用**同一个 agg** 聚合；基线用**日历月平移**而非固定 30 天
- **数值变换**：`value = raw × mul + add`，变换后的值参与环比与阈值判定
- **单位换算**（纯展示派生）：`none` / `auto`（K/M/G/T，可选 1000 或 1024 进制）/ `fixed`。
  事实层存原始值，展示层另给 `display_value`，**Word / 图表 / AI / 页面四处口径一致**
- **显著性判定**：按 `|环比变化|` 阈值挑出需要 AI 点评的指标，未超阈值的只给事实不给文字
### 3. AI 洞察摘要

- **只写文字，不参与计算**：送给模型的是结构化 facts，数值一个都不许它自己算
- **系统提示词页面化**：两段规则（基础 / 追加）全局可编辑，模板级还能再覆盖。
  **三层回退**：绑定级 → 全局 → 代码内置默认，改完**立即生效，无需发版重启**
- **改完直接试跑**：选模板 + 槽位 + 周期，用真实 facts 出稿；不落库、不生成 docx
- **版本与回滚**：`ai_prompt_versions` 是 **append-only 账本**，记谁在何时改了什么，可逐条一键回滚
- **数字自检护栏**：输出里出现的数字必须能在 facts 中找到出处，否则拦截并纠正重试；
  抄用参考范文的示例数字、条目数超出范文骨架，同样会被拦下
- **曲线只送统计摘要**（`min/max/avg/last`），避免把几万个原始点塞进 prompt 让模型自己找峰值
- **调用留痕**：每次请求的参数、messages、响应原文、重试过程都可查

### 4. Word 模板与绑定

- 上传 `.docx` → **自动扫描 `{{ 占位符 }}`**
- **在线编辑 / 预览原始文档**（ONLYOFFICE Document Server），保存即落为**新版本**并重扫占位符、自动清理失效绑定
- 版本历史 + **一键回滚**到任意历史版本
- 两类绑定：
  - **指标绑定** —— 选指标 + 单位 + 显示精度 + 数值变换
  - **AI 摘要绑定** —— 每个摘要槽位独立配写作要求 / 参考范文 / 字数 / 温度 / 波动阈值
- **占位符独占约束**：图表变量必须独占一个段落（否则 docxtpl 会把 140mm 宽的图挤在文字中间，版式不可控），
  绑定时直接校验拒绝，返回 422
- **授权**：模板级 `edit` / `view` 授权，与 admin / 属主组成三层权限

### 5. 图表渲染

- `curve` / `pie` 指标用 matplotlib 渲染成 **PNG 字节**，`docxtpl InlineImage` 内嵌，**不落临时文件、无缓存失效问题**
- 镜像内置 `fonts-wqy-microhei` 中文字体（+1.6MB），避免图表中文变成豆腐块
- 图内标注复用同一套统计函数 —— 同一张图不会出现两个峰值

### 6. 报告生成与推送

- 报告任务按 **cron 调度**（APScheduler，DB 是唯一真相源），支持**月报 / 周报**两种粒度，可设周期偏移（如上周）
- 同步生成 Word → 邮件推送 → **确认链接**（短期一次性 token）→ 未确认自动**催办**
- 全程写 `PushRecord`：确认邮件、催办提醒、重推都有记录，可在页面上查每一次发送
- 容器重启后调度状态可从库里恢复，不会重复触发或漏跑

### 7. 页面化配置

「系统设置」页可直接编辑并**立即生效**（无需重启）：

- **邮件集成**（SMTP）—— 密码留空表示不修改，不是清空
- **AI 集成** —— 任意 OpenAI 兼容端点（base_url / model / key / 超时）
- **AI 系统提示词** —— 两段规则编辑 + 内置默认查看 + 历史版本回滚
- 运行时配置落 `app_settings` 表，密钥字段 Fernet 加密，接口只回 `*_secret_set` 布尔

### 8. 权限模型

| 角色 | 能力 |
| --- | --- |
| `admin` | 全部：用户管理、系统设置、所有模板 |
| 属主（`manage`） | 自己模板的上传 / 版本 / 绑定 / 授权 / 删除 |
| 被授权 `edit` | 编辑文档 + 配置绑定 |
| 被授权 `view` | 只读 |
| 登录用户 | 创建报告任务、确认报告、查自己的历史 |

密码 **argon2id 单向哈希**，忘记无法找回明文，只能重置。

---

## 数据是怎么流动的

```
Zabbix API --> adapter --> calc_engine --> MetricFact[] --+--> docxtpl --> 月报.docx --> mailer --> 收件人
  (trend.get)   (区间语义)    (两阶段聚合)      (事实层)      |                |
                                                单位换算/数值变换              +--> matplotlib --> 内嵌 PNG
                                                          |
                                                          +--> ai_insight --> 洞察文字（只读facts，禁止自行推算）
                                                                               |
                                                                    数字自检护栏 <--+ 对不上就纠正重试
```

**一句话记住红线**：数值全部来自左边的 `calc_engine`，AI 只在右边写字，且它写的每个数字都要能指回 `MetricFact`。

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.11 · FastAPI · SQLAlchemy 2（async）· Alembic · APScheduler · Pydantic v2 |
| 数据库 | PostgreSQL 16 |
| 前端 | Vue 3 script setup · TypeScript · Element Plus · Pinia · Vue Router · ECharts |
| 文档 | python-docx / docxtpl · matplotlib · ONLYOFFICE Document Server（可选） |
| AI | OpenAI 兼容协议（OpenAI / DeepSeek 等任意兼容端点） |
| 部署 | docker compose（PostgreSQL + backend + nginx 前端） |

---

## 安装与运行

### 前置要求

- Docker + Docker Compose
- Python >= 3.11（本地开发用）
- Node.js >= 20（本地开发前端用）

### 1. 拿代码

```bash
git clone https://github.com/Miata-RX7/ReportPilot.git
cd ReportPilot
```

### 2. 生成两个必填密钥

```bash
# ENCRYPTION_KEY —— 用于加密数据源凭据 / SMTP 密码 / AI Key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# JWT_SECRET —— 用于签发登录令牌
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### 3. 写 `.env`

```bash
cd backend
cp .env.example .env
```

把上一步两行输出填进 `.env`：

```ini
ENCRYPTION_KEY=<第 2 步的 Fernet 密钥>
JWT_SECRET=<第 2 步的随机串>
```

> 这两个密钥**缺失或格式非法会启动即报错**（fail-fast），不会静默降级。
> `.env` 已在 `.gitignore` 里，**不要提交**。

### 4. 起服务

**方式 A · 全容器（推荐）**

```bash
cd backend
docker compose --profile prod up -d --build
docker compose exec backend alembic upgrade head
```

打开 <http://localhost:8080>。

**方式 B · 本地开发（前端热更新）**

```bash
# 终端 1 —— 只起数据库，后端在宿主机跑（.env 与热重载都最直观）
cd backend
docker compose up -d db
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload            # http://localhost:8000
```

```bash
# 终端 2 —— 前端
cd frontend
npm install
npm run dev                             # http://localhost:5173
```

`vite.config.ts` 已把 `/api` 代理到 `http://localhost:8000`，无需处理 CORS。

### 端口

| 服务 | 端口 |
| --- | --- |
| 前端（nginx） | `8080` |
| 后端 API | `8000` |
| PostgreSQL | `5432` |
| 前端 dev server | `5173` |
| ONLYOFFICE（可选） | `8081` |

---

## 配置项

完整列表见 `backend/.env.example`，最常改的几项：

| 变量 | 必填 | 说明 |
| --- | :---: | --- |
| `ENCRYPTION_KEY` | 是 | Fernet 密钥，加密一切敏感凭据 |
| `JWT_SECRET` | 是 | 登录令牌签名密钥 |
| `DATABASE_URL` | | 默认指向本地 `reportpilot` 库 |
| `STORAGE_DIR` | | Word 模板与生成报告的存放目录，默认 `./storage` |
| `AI_BASE_URL` / `AI_MODEL` / `AI_API_KEY` | | 任意 OpenAI 兼容端点；也可在系统设置页填 |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM` | | 邮件推送；也可在系统设置页填 |
| `APP_BASE_URL` | | **浏览器与邮件可达**的对外地址，用于拼确认链接 |
| `REPORT_TIMEZONE` | | cron 求值时区。容器是 UTC，不设会导致时刻错位 8 小时 |
| `SCHEDULER_ENABLED` | | **多副本部署时只在一个副本设 true**，否则任务会重复触发 |
| `PUBLIC_BASE_URL` | | ONLYOFFICE 回拉后端文件 / 回调用的地址（容器内网名） |
| `ONLYOFFICE_URL` / `ONLYOFFICE_INTERNAL_URL` / `ONLYOFFICE_JWT_SECRET` | | 三项齐全才启用在线编辑，否则自动降级为只读预览 |

> **页面优先**：`SMTP_*` 与 `AI_*` 在系统设置页保存后会**覆盖 `.env` 的默认值**并立即生效
> （密钥字段加密落库，接口只回是否已配置），不需要重启服务。

---

## 创建账号

系统**不预置任何账号**，用 CLI 创建：

```bash
cd backend

# 创建（密码交互式输入，不进 shell history）
docker compose exec backend python -m app.cli create-user --username admin --role admin

# 忘记密码时重置
docker exec -it reportpilot-backend python -m app.cli reset-password --username admin

# 临时签发令牌排查问题
docker compose exec backend python -m app.cli issue-token --username admin --days 7
```

`--role` 取 `admin` 或 `user`。密码 **argon2id 单向哈希**，忘记无法找回明文，只能重置。

---

## ONLYOFFICE：模板在线编辑（可选）

不部署也能用 —— 只是预览原始文档 / 在线编辑会降级为只读预览。

```bash
docker run -d --name reportpilot-ds \
  --network backend_default --network-alias onlyoffice \
  -p 8081:80 \
  -e JWT_ENABLED=true -e JWT_SECRET=<与 ONLYOFFICE_JWT_SECRET 一致> -e JWT_HEADER=Authorization \
  onlyoffice/documentserver:latest
```

后端 `.env` 补四项。三个容易踩的点：

1. `ONLYOFFICE_URL` 是**浏览器**可达的地址（`http://localhost:8081`）
2. `ONLYOFFICE_INTERNAL_URL` 是**后端容器**可达的地址（`http://onlyoffice`）。
   DS 回调里的下载 URL 用的是它自己的视角（常是 `localhost`），后端拿那个 host 连不通，
   会返回 `{"error":1}`；后端会把这个 URL 的 scheme+host 改写成内网可达地址，path/query 原样保留
3. `PUBLIC_BASE_URL` 是 DS 回拉后端文件 / 回调的后端地址

保存语义：status=2（用户保存）会下载并落为新版本；status=6（强制保存）只确认不建版本；
status=3/7（出错）只记日志不重试 —— 一次编辑只产生一个版本。

---

## 验证安装是否成功

```bash
cd backend

docker compose ps                                              # 容器都在 Up
docker compose exec backend alembic current                    # 期望 0017 (head)
curl -s http://localhost:8080/api/health                       # 期望 {"status":"ok",...}
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/  # 期望 200
```

然后走一遍最小闭环：

1. 数据源管理页建一个 Zabbix 数据源 —— 保存后凭据只回显末 4 位
2. 指标管理页建一个 `scalar` 指标 —— 试算通过
3. 模板管理页上传 Word —— 自动扫出占位符 —— 配置绑定
4. 报告任务页建一个月报任务 —— 生成 docx —— 在报告详情里核对 facts 与 AI 文字

---

## 开发与测试

```bash
# 后端（基线 711 passed）
cd backend
pytest

# 前端（199 个 vitest 用例）
cd frontend
npm test
npm run typecheck
npm run build
```

> `backend/` 与 `frontend/` 里**不要留** `_*.py` 这类一次性脚本 —— 它们不被 pytest 收集，
> 却会污染目录并跟着仓库分发。临时脚本请放进 `_attic/`（已 gitignore）。

---

## 安全须知

- **`.env` 不入库**（已在 `.gitignore`）；`ENCRYPTION_KEY` / `JWT_SECRET` 必须显式配置，缺失即启动失败
- 数据源 token、SMTP 密码、AI API Key 一律 **Fernet 加密**落库；
  接口只返回 `secret_hint` 或 `*_secret_set` 布尔，**任何响应、日志、异常里都不出现明文**
- 密码 **argon2id 单向哈希**
- 日志中不出现 `Authorization` / `Bearer` 字样
- 报告文件与 ONLYOFFICE 回调用**短期一次性 token**，回调另有 JWT 校验
- **AI 不算数**：数值由汇算引擎产出，AI 只写文字，且其输出的每个数字都必须能在 facts 里找到出处

---

## 延伸文档

| 文档 | 内容 |
| --- | --- |
| [`docs/technical-reference.md`](docs/technical-reference.md) | 技术参考：API 一览、权限矩阵、指标类型与 `MetricFact` 结构、图表渲染硬约束、升级步骤、部署清单、已知技术债 |
| [`docs/smoke_test.md`](docs/smoke_test.md) | 端到端冒烟测试步骤（13 步） |
| [`docs/smoke_test_auth.md`](docs/smoke_test_auth.md) | 带认证的冒烟测试变体 |
| [`docs/smoke_test_report.md`](docs/smoke_test_report.md) | 冒烟测试执行报告与已修缺陷 |
| [`AGENTS.md`](AGENTS.md) | 项目上下文、不可破坏的红线、已知坑（给 AI 助手与接手人看） |
| [`ReportPilot_Design_Token.md`](ReportPilot_Design_Token.md) | 设计 token 规范（`frontend/src/styles/tokens.scss` 的源头） |

## License

[MIT](LICENSE)