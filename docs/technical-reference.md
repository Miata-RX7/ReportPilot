# ReportPilot · 技术参考

> 本文是**技术参考手册**：API 一览、指标类型与数据结构、图表渲染硬约束、升级步骤、部署清单、已知技术债。
> 项目能力介绍与安装方法见根目录 [`README.md`](../README.md)。

月报自动化生成系统：数据接入 → 数据汇算 → AI 洞察摘要 → 模板生成 → 审核推送。
AI 仅用于辅助生成分析文字，不参与任何指标计算（facts/insights 强制分离）。

## 快速开始（开发）

```bash
cd backend
cp .env.example .env   # 填写 ENCRYPTION_KEY（必须）等配置
docker compose up -d db
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

生成 ENCRYPTION_KEY：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

生成 JWT_SECRET：

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

创建管理员账号并获取 token：

```bash
docker compose exec backend python -m app.cli create-user --username admin --role admin
docker compose exec backend python -m app.cli issue-token --username admin --days 7
```

## 用户与密码管理（CLI）

密码以 argon2id **单向哈希**存储，忘记后无法找回明文，只能重置：

```bash
# 交互式重置（推荐：密码不回显、不进 shell history）
docker exec -it reportpilot-backend python -m app.cli reset-password --username ops

# 自动化脚本才用 --password（会进入 shell history，谨慎使用）
docker exec reportpilot-backend python -m app.cli reset-password --username ops --password 'NewPass123'
```

- 只改密码，**不改**角色与启用状态；需要改角色用 `create-user` 语义外的直接维护。
- 兼容历史 `password_hash` 为空的账号（如种子 `admin`），此时输出"密码已设置"。
- 密码长度至少 8 位；命令不会回显密码，也不写入日志。
- 仍无法登录时（用户被停用等），用 `issue-token` 临时签发 JWT 排查。

## 前端

两种运行模式：

**开发**（容器只跑 db + backend，前端在宿主机热更新）：

```bash
# 终端 1
cd backend && docker compose up -d db backend
# 终端 2
cd frontend && npm install && npm run dev    # http://localhost:5173
```

`vite.config.ts` 已配置 `/api` 代理到 `http://localhost:8000`，无需处理 CORS。

**生产**（nginx 独立容器，FastAPI 只管 API）：

```bash
cd backend && docker compose --profile prod up -d --build
# 前端 http://localhost:8080，/api 由 nginx 反代到 backend:8000
```

前端质量检查：

```bash
cd frontend
npm test          # vitest：utils 与 axios 拦截器
npm run typecheck # vue-tsc
npm run build     # 类型检查 + 生产构建
```

## 模板在线编辑（ONLYOFFICE Document Server）

模板弹窗内"预览原始文档"与"在线编辑"由 ONLYOFFICE Document Server（DS）驱动：
预览用只读 viewer（`mode=view`），编辑用 `mode=edit`；编辑保存时 DS 回调后端，
落盘为**新版本**并重扫占位符、自动清理失效绑定。

**部署 DS**（与后端同网络，浏览器可达映射端口）：

```bash
docker run -d --name reportpilot-ds \
  --network backend_default --network-alias onlyoffice \
  -p 8081:80 \
  -e JWT_ENABLED=true -e JWT_SECRET=<与后端一致的密钥> -e JWT_HEADER=Authorization \
  onlyoffice/documentserver:latest
docker exec reportpilot-backend python -c "import urllib.request; print(urllib.request.urlopen('http://onlyoffice/healthcheck').read())"
```

**后端 `.env` 配置**（三项齐全才启用，否则前端降级为提示"未配置"）：

| 变量 | 说明 | 示例 |
| --- | --- | --- |
| `ONLYOFFICE_URL` | **浏览器**可达的 DS 地址（前端加载 `api.js`） | `http://localhost:8081` |
| `ONLYOFFICE_INTERNAL_URL` | **后端容器**可达的 DS 地址（下载保存回调里的文件） | `http://onlyoffice` |
| `ONLYOFFICE_JWT_SECRET` | 与 DS 的 `JWT_SECRET` 一致 | 32+ 字符随机串 |
| `PUBLIC_BASE_URL` | **DS** 回拉后端文件/回调的后端地址 | `http://reportpilot-backend:8000` |

说明：容器内联调时 `ONLYOFFICE_URL` 需指向浏览器可达名字（如网络别名 `http://onlyoffice`）；
宿主机浏览器使用 `http://localhost:8081`。DS 拉取文件与回调均使用短期 HMAC 一次性 token，
回调体再做 JWT 校验。

> **`ONLYOFFICE_INTERNAL_URL` 为何必需**：DS 回调里的文档下载 URL 由 DS 按**自己的视角**
> 生成（host 常是 `localhost` 或容器本地名）。后端容器解析不了这些名字，会导致
> `保存文件时出错`（回调返回 `{"error":1}`）。后端会把该 URL 的 scheme+host 改写成
> `ONLYOFFICE_INTERNAL_URL`，path/query（含 md5/expires 校验参数）原样保留；未配置时
> 退回使用 DS 原始 URL。

### 保存语义（避免一次编辑产生多个版本）

| 回调 status | 含义 | 行为 |
| --- | --- | --- |
| 2 | 用户点保存 / 关闭文档 | 下载 → 重扫占位符 → **落盘为新版本** |
| 6 | 强制保存（`forcesave` 定时触发） | 直接确认 `{"error":0}`，**不下载、不建版本** |
| 3 / 7 | 保存出错 | 记日志并确认，避免 DS 反复重试 |

另外，status=2 下载到的内容若与当前版本字节完全一致（DS 关闭时可能重复回调），
会跳过新建版本。因此一次编辑只产生一个版本。

## 升级步骤（阶段 3 图表渲染）

从旧版本升级到含图表渲染的版本，**必须按顺序做两步**，否则会出现
"前端看不到独占/混排标记"或"容器重启后 matplotlib 丢失"：

```bash
# 1) 重建后端镜像并重建容器（阶段 3 新增了 matplotlib 与中文字体层，
#    旧镜像里没有；仅 restart 容器不会带入新镜像内容）
docker compose build backend
docker compose up -d --force-recreate backend

# 2) 重建前端镜像（绑定矩阵的独占/混排标记、饼图图标等前端资源）
docker compose build frontend
docker compose up -d --force-recreate frontend
```

3. 浏览器 **Ctrl+F5 强刷**（模板页的独占/混排标记依赖新的 JS 产物）。

**验证是否升级成功**：
- 打开模板管理 -> 任一模板 -> 「配置绑定」，每个占位符左侧应出现
  `独占`（绿）或 `混排`（黄）标签
- 后端健康：`docker exec <backend> python -c "import matplotlib; print(matplotlib.__version__)"`

> 字体说明见下方「图表渲染」小节；若出现生僻字豆腐块，需改用
> `fonts-noto-cjk` 重新构建后端镜像。

## 图表渲染（曲线图 / 饼图 -> Word 内嵌 PNG）

绑定到模板的 curve / pie 指标会以 **PNG 图片**插入 Word（matplotlib 渲染 +
docxtpl `InlineImage`）。相关约束与实现要点：

**段落独占要求（硬约束）**
`{{ var }}` 必须在 Word 里**独占一个段落**（去掉首尾空白后整段只有这个变量）。
绑定时校验，混排返回 422。原因是 docxtpl 0.20.2 对混排**也会**插图（不会静默
跳过），但 140mm 宽的行内图片会被挤在文字中间，版式不可控。若将来确需混排，
可加 `allow_inline_chart` 逃生开关（当前不做）。

**PNG 不落盘**：`chart_renderer.render_fact_chart()` 返回 PNG **字节**，
调用方用 `BytesIO` 包成 `InlineImage`。没有临时文件、没有清理逻辑、没有孤儿
文件；报告重新生成时重跑 `compute` -> 重新画即可，不依赖任何缓存失效。

**数字格式化的两套口径（有意不一致，勿"顺手统一"）**
- **Y 轴刻度**：辅助参考 -> 紧凑格式（`1234567 -> 1.23M`）。曲线原始值常是 bps
  量级，按 `display_precision` 会得到 `5000000.00` 这类长刻度，反而更难读。
- **数据标注**（图内最大/均值/末值）：精读值 -> 千分位 + 最多 2 位小数
  （`4,862,609`）；指标配了 `display_precision` 时按其位数。与
  `format_fact_value` 的 `%g` 略有差异是刻意的：图上要可读性。

**中文字体**
镜像内安装 `fonts-wqy-microhei`（仅 +1.6MB，覆盖 GB2312 + 常用简中），
字体链 `['WenQuanYi Micro Hei', 'DejaVu Sans']`，并设 `axes.unicode_minus=False`
（否则负号显示为方块）。**若实测出现生僻字豆腐块**，改用 `fonts-noto-cjk`
（+56MB 下载 / 解压约 200MB，覆盖更全），改 Dockerfile 后重新构建镜像即可。
字体缺失不阻塞报告生成：仅记 warning 并退回默认字体。

**其它**
- `InlineImage` 与 `SandboxedEnvironment` **兼容**，无需为图片放宽 Jinja 沙箱。
- 构造 `InlineImage` 前后**不要**调用 `tpl.get_xml()`（会让 `_inline_shapes`
  置空，后续插图报 `'NoneType' object has no attribute '_element'`）。
  正确模式：`open_template()` -> 造 `InlineImage` -> `render_docx(tpl=同一实例)`，
  preview 与 report 两条链路都遵循此模式。
- 多个图表要产生多个 `word/media` 条目，必须用**不同内容**的图片（python-docx
  按内容哈希去重）。
- 独占信息（`is_alone_in_paragraph`）**不落库**，按需解析当前版本 docx 得出，
  始终反映文件真实状态；`placeholders_json` 仍是 `list[str]`，无数据迁移。
- 曲线/饼图指标在报告详情页的 facts 表里只给摘要行，facts 行左侧有趋势图图标。

## 安全注意事项

- `ENCRYPTION_KEY`、`JWT_SECRET`、`SMTP_PASSWORD`、`ONLYOFFICE_JWT_SECRET` 等敏感配置只放
  `.env`（已在 `.gitignore`），**禁止提交到 Git**。
- 数据源 API token 以 Fernet 加密存储（`secret_encrypted`），接口只返回脱敏提示。
- 系统设置页保存的 SMTP 密码 / AI API Key 同样以 Fernet 加密落库（`app_settings` 表，
  迁移 `0012`），接口只返回"是否已配置"；再次编辑时留空表示**不修改**原密钥。
  `.env` 仍是默认值来源，页面保存的项覆盖默认值，无需重启服务。

## 部署清单（生产前必读）

- **自签名证书**：若 Zabbix / AI 端点使用自签名证书，需把站点 CA 证书挂入
  backend 容器（如挂载到 `/usr/local/share/ca-certificates/` 并
  `update-ca-certificates`），然后开启 `verify_ssl=true`。
  `verify_ssl=false` 仅限测试环境（Phase 2 处理，Phase 1 接受）。
- **生产 PG 日志收紧**：默认 PG 日志可能记录完整查询字符串（如报错语句）。
  生产环境建议调整 `log_min_error_statement=error`、关闭
  `log_statement`，避免 `db.log` 出现查询内容（Phase 2 处理，Phase 1 接受）。

## Phase 1 临时方案声明

- **issue-token**：前端登录页 Phase 2 才做，过渡期用
  `python -m app.cli issue-token` 手动签发 JWT 供调试；登录页上线后移除该子命令。
- **登录防暴力**：login 端点仅有固定 500ms 延时，Phase 2 换正式限流。
- **报告同步生成**：`POST /api/reports` 同步等待完成，部署时反向代理 /
  uvicorn 单请求超时应 >= 5 分钟；前端调用期间需展示"生成中"提示。

## 技术债（Phase 2 偿还）

- `frontend/src/components/DataTable.vue` 的 `data` 属性放宽为 `any[]`，
  待各列表实体类型稳定后改为泛型 `DataTable<T>` 以获得行级类型检查。
- 前端 UI 验证脚本（headless chrome）的行匹配目前按 `name` 子串，
  存在同名/前缀重复时误命中风险，应改为按后端 `id` 定位行。

## 文件命名约定（API）

- **预览文件**：`preview_{模板名}_{period}.docx`
  由 `POST /api/templates/{id}/preview` 通过 `Content-Disposition: filename=`
  返回（如 `preview_运维月报模板_2026-08.docx`）。比用 id 命名更直观，
  且与下方报告邮件附件命名口径一致。前端 `TemplatesView` 下载时优先取
  `Content-Disposition` 中的该文件名。
- **报告产物**：`report_{报告id}_{period}.docx`
  由 `GET /api/reports/{id}/download` 返回；推送邮件的附件名则用
  `{模板名}_{period}.docx`（同预览口径，不含 id）。

## 模板版本与授权（API）

数据模型：`WordTemplate`（逻辑模板：名称/属主/当前版本快照）+ `TemplateVersion`
（版本：文件与占位符，支持回滚）+ 版本级 `TemplateBinding` + `TemplateGrant`（授权）。
`Report` 记录 `version_id`，生成报告使用模板的**当前版本**，历史报告可追溯当时版本。

权限：admin 与属主 = `manage`；被授权 `edit` = 可编辑文档 + 绑定；被授权 `view` = 只读。
列表与所有操作均按权限过滤。

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/templates` | 登录用户 | 上传（上传者成为属主），创建版本 v1 |
| GET | `/api/templates` | 登录用户 | 仅返回可见模板（属主/被授权/管理员） |
| GET | `/api/templates/{id}` | view | 详情（含当前版本、绑定、权限） |
| GET | `/api/templates/{id}/file` | view（或签名 token） | 原始 docx |
| POST | `/api/templates/{id}/editor?mode=view\|edit` | view / edit | ONLYOFFICE 配置（含签名 JWT） |
| POST | `/api/templates/{id}/callback?token=` | 签名 token | DS 保存回调 → 新版本 |
| GET | `/api/templates/{id}/versions` | view | 版本列表 |
| POST | `/api/templates/{id}/versions/{vid}/activate` | edit | 回滚为当前版本 |
| PUT | `/api/templates/{id}/bindings` | edit | 全量替换**当前版本**绑定 |
| GET/PUT | `/api/templates/{id}/grants` | manage | 查看/全量替换授权 |
| POST | `/api/templates/{id}/transfer` | manage | 转移属主 |
| DELETE | `/api/templates/{id}` | manage | 软删除 |
| GET | `/api/users` | 登录用户 | 用户列表（授权选择用） |

新版本语义：以当前版本为基准复制绑定，按新占位符集合自动清理失效项（`cleared`）；
新增变量需重新配置绑定。

## 指标类型（metric_type）

指标按用途分五类，决定 `query_config` 形态与 `calc_engine` 的计算分支：

| 类型 | 用途 | 取数配置 | `MetricFact.value` | 状态 |
| --- | --- | --- | --- | --- |
| `scalar` | 数值（多 item 聚合） | `item_ids` 多选 | `float`（已应用 `transform`） | 阶段 1 |
| `text` | 文本（最新值 / 固定文案） | `item_ids` 0~1 个 | `{"text": str}` | 阶段 1 |
| `curve` | 折线图（每 item 一条曲线） | `series[]` | `{"series": [...]}` | 阶段 2 |
| `pie` | 饼图（UP/DOWN 占比） | `item_ids` 恰好 1 个 | `{"up_count","down_count","up_pct","down_pct"}` | 阶段 2 |
| `time` | 时间（当前时间 + 偏移 + 自定义格式） | **无**（不取数） | `{"text": str}`（复用 text 形态） | 阶段 4 |

### time（时间类型）

不取数、不需要数据源：基准是**计算时刻**（试算 / 生成报告的那一刻），按偏移量平移，
再用自定义格式模块渲染成文本。

```jsonc
{
  "metric_type": "time",
  "data_source_id": null,          // 唯一允许为空的类型
  "query_config": {},              // 不取数
  "type_config": {
    "time_config": {
      "format_modules": ["年", "-", "月"],  // 有序，可重复
      "zero_pad": true,                     // 月/日/时/分/秒补零；年始终 4 位
      "offset_value": -1,                   // 正数向后、负数向前
      "offset_unit": "month"                // day | month
    }
  }
}
```

- **模块词表**：字段 `年 月 日 时 分 秒` + 字面量 `空格 - / : .`；白名单校验，
  非白名单一律 422。同一模块可重复（`[年, 年]` -> `20262026`）。
- **示例**：`2026-10` 偏移 `-1 月` -> `2026-09`。
- **补零**：`zero_pad=true` 时 `2026-09`；关闭时 `2026-9`（年始终 4 位）。
- **月偏移自动夹取月末**：`2026-03-31 -1 月` -> `2026-02-28`（闰年 `2028-02-29`），
  不会溢出到 3 月（后端用 `relativedelta`，前端用 `setMonth` + 月天数夹取）。
- **基准是计算时刻**（不是统计周期）：同一指标在不同时刻试算可能得到不同结果，
  报告重新生成时也会重算。`MetricFact.object` / `formula` 会写明基准时刻与偏移量，
  便于追溯。`formula` 形如 `now(-1月) -> format('年-月')`。
- **渲染口径双实现**：后端 `calc_engine.format_time_modules` 与前端
  `utils/time.ts::formatTimeModules` 语义必须一致，两侧都有同批单测锁定。
- 复用 `value_shape="text"`，因此可直接绑定到模板变量（不需要独占段落），
  报告里就是一段文本。

### 通用约定
- **`POST /api/metrics` 与 `PUT /api/metrics/{id}` 必须显式传 `metric_type`**（breaking change，
  缺省返回 422）。后端用 Pydantic discriminated union 精确校验；**库内历史数据**由
  迁移 `0008` 回填为 `scalar`，库级 CHECK 约束 `ck_metric_type` 保证合法值。
- `calc_formula`（agg/compare/baseline_lookback）**仅 scalar 使用**；其余配置放
  `type_config`（`transform` / `text_config` / `pie_config` / `transform_default` /
  `time_config`）。
- `data_source_id` 对 `time` 类型允许为 `null`（迁移 `0009` 放宽 NOT NULL），
  **其余类型必填**，缺失返回 422。
- scalar 数值变换：`value = raw * mul + add`（本期与基线同口径，`change_pct` 基于变换后数值）。

### curve（阶段 2）
- 所有 series 的 `item_id` **合并成一次 `trend.get`**（N 条曲线仍 1 次 RPC）。
- 逐点应用该 series 自己的 `transform`；`points` 按 unix_ts **升序**。
- **缺失小时不补点**（不插 null）；前端 ECharts 用 `xAxis.type='time'`（多序列时间戳
  可能不对齐，category 轴会误导）。
- 上限 `type_config.max_series`（默认 12），超限直接报错；不做降采样（一个月最多
  744 点/series）。不做基线对比，`baseline_value` / `change_pct` 恒为 `null`。

### pie（阶段 2）
- 单 item；**统计口径用 `value_max`**：该小时命中 `up_values` 记 UP，命中
  `down_values` 记 DOWN。不用 `value_avg` 是因为 0/1 型 item 在某小时内既有 0 又有 1
  时 avg 会得到 0.5，使"小时数占比"失真。
- `unknown_mode`：`exclude` 未命中小时不计入分母；`error` 直接报错。
- `up_pct` / `down_pct` 保留位数跟 `display_precision`：`null` → 1 位，指定 N → N 位。
- 前端 ECharts 饼图渲染（`echarts/core` + `PieChart` 按需引入）。

### pie 适用性判定
`POST /api/datasources/{id}/analyze-pie-suitability`，body `{host_group_ids, host_ids, item_id}`。
- 窗口 **7 天**、超时 **5 秒**；超时返回 `suitable="unknown"`，不硬失败。
- 判定顺序：非数值型（`value_type` ∉ {0,3}）→ 不适合；读 item 的 value map
  （Zabbix 6.0+ 走 `selectValueMap`，取 `valuemap.mappings[]` 的 `value`→`newvalue`）；
  统计 `value_max` 的 distinct：2 个 → high（建议小值 DOWN、大值 UP），3~5 个 →
  medium（不自动填，由用户确认），>5 个 → 不适合。
- `key_` 命中 `icmpping/icmp/ping/status/state` → 置信度提升一级。
- 前端：`suitable=false` 展示原因，需勾选「我了解风险」才允许保存（不硬阻断）。

### 模板绑定护栏
阶段 3 图表渲染上线前，**curve / pie 指标禁止绑定到模板**（`PUT /api/templates/{id}/bindings`
返回 422），避免产出把结构化 dict 塞进 Word 的坏报告。绑定页对这两类指标显示
「阶段 3 支持绑定模板」标注，阶段 3 放开。

### MetricFact.value 与 value_shape

`MetricFact.value` 为 **`float | dict`**，由同级的 **`value_shape`** 告知消费端如何解析：

| `value_shape` | `value` 原始结构 | 消费方 |
| --- | --- | --- |
| `scalar` | `float` | 按 `display_precision` 格式化 |
| `text` | `{"text": "本月无异常"}` | 取 `payload.text`（**不是裸字符串**） |
| `series` | `{"series": [{"name","item_id","points": [[ts, v], ...]}]}` | 阶段 2 折线图 / 阶段 3 图表 PNG |
| `pie` | `{"up_count","down_count","up_pct","down_pct"}` | 阶段 2 饼图 / 阶段 3 图表 PNG |

- 字段名说明：设计稿里该字段名为 `value_type`，实现改名为 **`value_shape`**，以免与
  Zabbix 监控项的 `value_type`（`0`=浮点 / `3`=无符号整数）混淆。语义与设计一致。
- **历史兼容**：`GET /api/reports/{id}` 原样返回落库的 `facts_json`，**不会回填
  `value_shape`**；缺该字段时一律**按 `scalar` 解析**（后端默认 `"scalar"`，前端
  `factShape()` 用 `?? 'scalar'`）。阶段 1 之前生成的报告无需任何数据迁移。

## 测试

```bash
cd backend
pytest
```
