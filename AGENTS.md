# AGENTS.md — ReportPilot 项目上下文（AI 助手入口）

> 任何 AI 助手 / 第三方模型接手本项目，**先读本文件**，再读 `DEPLOY.local.md`
> （服务器连接与外部服务参数，**本地私有、已加入 .gitignore，不要提交**）。

最后更新：2026-10-15

> ⚠️ **2026-10-15 事故**：本文档曾在一次 PowerShell 批量写文件时被截断（只剩末尾一节）。
> 第 1-6 节、5.1-5.6、5.10、5.11 为按原文重建；**5.7-5.9 与第 6 节是按 `DEPLOY.local.md`
> 的发布记录补写的浓缩版**，如与你的记忆不符，以 `DEPLOY.local.md` 与代码为准，并顺手订正这里。

---

## 1. 项目是什么

月报自动化系统：数据接入 → 数据汇算 → AI 洞察摘要 → 模板生成 → 审核推送。

- 后端 `backend/`：FastAPI + SQLAlchemy(async) + Alembic + APScheduler
- 前端 `frontend/`：Vue 3 `<script setup>` + TypeScript + Element Plus + Pinia + ECharts
- 编排：docker compose（postgres:16 + backend + 可选 frontend nginx）
- **AI 只生成文字，绝不参与任何指标计算**（facts / insights 强制分离）

关键目录：

| 路径 | 内容 |
|---|---|
| `backend/app/api/` | 一资源一文件路由；`deps.py` 提供 `get_current_user` / `require_admin` |
| `backend/app/services/` | `calc_engine` 取数汇算、`ai_insight` 摘要、`mailer` 邮件、`scheduler` 调度、`crypto` Fernet、`runtime_config` 配置覆盖层、`series_stats` 曲线统计、`unit_scale` 单位换算、`ai_trace` AI 留痕、`ai_prompt_service` 提示词版本 |
| `backend/app/config.py` | Pydantic Settings，`.env` 是**默认值**来源 |
| `backend/alembic/versions/` | 迁移，命名 `YYYYMMDD_NNNN_描述.py` |
| `backend/tests/` | pytest，**基线 711 passed**（在目标服务器裸容器里跑；本机只能单文件跑） |
| `frontend/src/views/` | 页面（`SystemSettingsView`、`ReportTasksView` 等） |
| `frontend/src/api/` | axios 封装，按资源分文件 |
| `docs/` | 端到端冒烟测试文档（`smoke_test*.md`） |
| `.workbuddy/memory/` | 项目长期笔记（`MEMORY.md` 是索引） |

---

## 2. 不可破坏的红线

1. **密钥只写不读**：任何接口、日志、异常、返回值都不得出现 token / 密码 / API Key 明文。
   数据源密钥对外只给 `secret_hint`（`****末4位`），密文存 `secret_encrypted`（Fernet）。
2. **AI 不算数**：指标数值由 `calc_engine` 产出；`ai_insight` 只做文字，且必须 `verify_numbers` 自检。
   （补充：system 规则文本虽已开放给管理员编辑，但这条红线的**代码层护栏仍在**——
   `verify_numbers` / `sample_leaks` / `sample_item_overrun` 不随提示词变化，见 5.11）
3. **取数两阶段聚合，顺序不可颠倒**：
   - 阶段一（时间，per item）：区间内每小时 trend 按 agg 聚合
   - 阶段二（空间，跨 item）：各 item 的周期值再用**同一 agg** 聚合
   - 列选择：`avg`/`sum`→`value_avg`；`max`→`value_max`；`min`→`value_min`
   - 基线用**日历月平移**（relativedelta），禁止固定 30 天；本期与基线必须同列同 agg
   - 区间语义要改就改 **adapter 层**，`period_utils` 的返回值语义已被 17 个单测固化
4. 日志中不得出现 `Authorization` / `Bearer` 字样。
5. `.env` 不入 Git；`ENCRYPTION_KEY` 与 `JWT` 密钥必须显式配置。

---

## 3. 已知坑（改代码前必读）

1. **Zabbix trend 桶边界**：桶以起始时刻为 clock，覆盖 `[clock, clock+3600)`；传 `time_till = 次月首日00:00` 会多计次月首桶。`adapters/zabbix_adapter.py` 已做 `-1` 修正。
2. `apiinfo.version` **不能带认证头**（Zabbix 7.x 返回 `-32602`），adapter 用 `auth=False`。
3. `trend.get` 对约 440 个 item 直接 HTTP 500。
4. `python:3.11-slim` 镜像需 `build` 才能拿到新增依赖（曾缺 `dateutil`、matplotlib）。
5. **不要在容器内直接跑 pytest**（会生成 root 属主 `.pytest_cache`，导致 uvicorn `--reload` 的 watchfiles 崩溃），用 `-p no:cacheprovider`。
6. DeepSeek `model` 字段必须传**模型 ID**（`deepseek-flash`），不是 `GET /models` 的展示名。
7. `backend/` 目录下有两个误生成的垃圾文件：`=3.9,`、`m_empty2.json`，可删。
8. **AI 数字自检必须先摘日期**：`_NUMBER_RE` 会把期间 `2026-09` 切成 `2026` 与伪负数 `-09`，范文里的 `2026年9月` 还会切出 `9`。
   任何新增的数字扫描入口都要先过 `ai_insight._mask_dates()`，否则会误报「未溯源」或触发无谓重试。
9. **本机 Python 3.14、生产镜像 3.11**：注解求值时机不同（PEP 649 延迟求值）。
   任何新增**带自定义类型注解的模块级函数**，都要在 3.11 容器里 `python -c "import app.main"` 过一遍——
   5.11 就因此让生产后端 502 过（`NameError`）。
10. **`get_settings()` 是 `@lru_cache` 单例**：测试里改它必须走 `monkeypatch.setattr`，
    直接赋值会污染整个 pytest 进程（表现为"本地全绿、服务器按字母序跑就红"）。
11. **system 文本不再走 `str.format()`**（5.11 起）：`render_system_prompt()` 只替换
    `{threshold_pct}` / `{max_chars}` 两个白名单变量，其余花括号原样保留。
    新增带自定义类型注解的模块级函数、或新增默认 system 文本时，别再引入第三个花括号变量。
12. **`v-if` 挂载的子弹窗 + 只监听 `modelValue` 的 watch = 首次打开必空**（2026-10-15 修）：
    父组件 `openAiConfig(row)` 在**同一个 tick** 里同时设 `aiCfgRow` 与 `aiCfgVisible`，而
    `<InsightConfigDialog v-if="aiCfgRow">` 是下个 tick 才创建的 —— 子组件**带着 `modelValue`
    已经为 true 被创建**，此后它不会再"变化"，所以没有 `immediate` 的 watch 一次都不触发，
    表单停在初始空对象。表现：**硬刷新后第一次点开「写作要求 / 参考范文」都是空的，关掉再点就正常**
    （关闭时 `aiCfgRow` 没被置 null，组件没卸载，第二次才走 `false → true`）。
    修法：`watch([() => props.modelValue, () => props.config], ..., { immediate: true, deep: true })`。
    **新写子弹窗照抄这个写法**；仓库里其它弹窗（`TemplateVersionsDialog` / `TemplateGrantsDialog` /
    `TemplatePreviewDialog` / `OnlyofficeEditorDialog` / `ReportDetailDialog`）都已有 `immediate: true`。
    另：vitest 是 `environment: 'node'` 且没装 `@vue/test-utils`，**组件层没有单测**，这类时序 bug 只能靠真实点击验收。

---
## 4. 本地环境现状（2026-10-14 实测）

| 项 | 状态 |
|---|---|
| Docker 引擎 | **未运行**（`dockerDesktopLinuxEngine` 连接失败）→ 本机无法起服务 |
| 前端 node / node_modules | npm 不在 PATH；改用 `~/.workbuddy/binaries/node/versions/24.14.0`（v24.14.0 + npm 11.9）。`npm ci` 约 30s，之后 `typecheck` / `test` / `build` 均可用 |
| 后端 `backend/.venv` | 存在（**Python 3.14.5**），但**缺 matplotlib** → 整包 pytest 收集即失败；**单文件可以跑** |
| git 仓库 | 无任何提交、无 remote（全部文件 untracked）→ 没有"推代码即发布"通道 |

结论：**本机已能做大部分验证** —— 前端全绿；后端整包 pytest 仍不可用（缺 matplotlib），
改为「本机跑不依赖 matplotlib 的单文件 + 服务器裸容器跑全量」。

```powershell
# 前端（PowerShell，用 workbuddy 自带 node；npm 不在 PATH）
$n="$env:USERPROFILE\.workbuddy\binaries\node\versions\24.14.0"; $env:PATH="$n;$env:PATH"
cd frontend; npm ci; npm run typecheck; npm test; npm run build
# 后端：本机只能跑不依赖 matplotlib 的单文件
cd backend; .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q tests/test_period_utils.py
# 后端全量：服务器裸容器（勿用 docker compose run，见 5.9）
docker run --rm -v /opt/reportpilot/backend:/code -w /code reportpilot/backend:latest python -m pytest -p no:cacheprovider -q   # 基线 711 passed
```

> ⚠️ **本机 venv 是 3.14、生产是 3.11**：本机跑得过不代表线上起得来（见 3.9）。
> 部署前后的最终判据是服务器裸容器里的 `711 passed` + `import app.main`。

---

## 5. 当前进度

### 5.1（2026-10-13，已上线）运行时配置页面化：邮件 / AI 集成可编辑

去掉「报告任务」页刷新按钮与「系统设置」页「刷新配置」按钮；邮件集成增加编辑（密码留空 = 不修改）；
「其它集成」里 AI 集成拆为独立卡片。

- 新增 `backend/app/models/app_setting.py`（`app_settings` 表）、`backend/app/services/runtime_config.py`（Fernet 加密 + 进程内缓存 + `effective_settings()`）、迁移 **0012**
- 修改 `backend/app/api/system.py`（`PUT /api/system/settings/mail`、`PUT /api/system/settings/ai`，均 `require_admin`）、`app/main.py`（启动载入覆盖项，失败不阻断）、`models/__init__.py`、`services/{mailer,ai_insight,task_service}.py`（`get_settings()` → `effective_settings()`）
- 前端 `views/SystemSettingsView.vue`、`views/ReportTasksView.vue`、`api/system.ts`

不可破坏的语义：可覆盖项白名单 `runtime_config.EDITABLE_KEYS`（非密钥明文存，密钥 Fernet 加密且接口只回 `*_secret_set` 布尔）；
**字段为 `None` 或空串 → 保持原值，不是清空**（`_secret_or_none()`）；`GET /api/system/settings` 顺带刷新缓存。

### 5.2（2026-10-13，已上线）指标单位换算（展示派生层）

把 `262468.9041666667 bps` 统一展示为 `262.47 Kbps`，Word / 曲线图 / AI 摘要 / 页面四处口径一致。

- 配置放**指标级** `metric_definitions.unit_config`（迁移 **0014**），三模式 `none` / `auto`（K/M/G/T，1000 或 1024）/ `fixed`
- **架构边界**：换算是纯比例，只做展示派生；`MetricFact` 的 `value` / `baseline_value` / `change_pct` 仍是原始口径，新增 `display_value` / `display_baseline_value` / `display_unit` / `display_factor`；消费端**优先展示字段、缺省回退原始字段**
- 出口唯一：`calc_engine._apply_unit_scale`；scalar 用本期值选档、基线复用同一因子；curve 用全点最大绝对值统一选档
- 新增 `services/unit_scale.py` + `frontend/src/utils/unit.ts`（两侧各有单测锁定）
- 与「数值变换」分工：`transform` 改**事实层**（参与环比与阈值），`unit_config` 只改展示

### 5.3（2026-10-13，已上线）前端单位选择器（Grafana 风格）

`单位`（可搜索分组下拉 + `allow-create`）+ `☑ 换算` + `进制` + `ⓘ`，只对 `scalar` / `curve` 显示整行（pie 恒 `%`、text/time 无单位）。

- 新增 `frontend/src/utils/metricUnits.ts`（`METRIC_UNIT_GROUPS` 是单位预置清单**唯一数据源**）
- 三条不可破坏的规则：① 只给基准单位配 `base`，`Kbps` / `MB` 这类自带前缀的置灰「换算」；② `suffix_unit` 提交时留空；③ 换算后小数位一律用「小数精度」
- **后端零改动**（契约不变，无迁移）
- 坑：`el-tooltip` 内容 teleport 到 body，样式要用 `popper-class` + **非 scoped** `<style>` 收口
### 5.4（2026-10-13，已上线）周报期次（period_granularity / period_offset，迁移 **0016**）

任务与报告支持"上周"粒度：调度按周任务错开触发，`period_utils` 提供周期间工具。
残留无害说明：升级前两列的默认值（`month` / `0`）正是升级前行为。

### 5.5（2026-10-14，已上线）曲线指标只送统计摘要

线上真实现象：`流量图` 的 3 万多个原始点全塞进 prompt（user message **340KB**），模型据此自己"找出"峰值，
还照范文句式补了一条独立的指标数据行（模板页故障：范文只写"平均速率"一条，输出多了一条"出方向峰值"）。

- 新增 `backend/app/services/series_stats.py`：每条曲线算 `min/max/avg/last`
- `build_payload` 对 `value_shape=series` 只送 `{"series": [{name,min,max,avg,last}]}`（744 点 → **711 字符**）
- `chart_renderer` 图内标注复用同一函数（同一张图不会出现两个峰值）
- system 规则 1 同步说明曲线摘要字段，并禁止模型自行推算（例如峰值出现的时间）
- 护栏 `ai_insight.sample_item_overrun`（+`item_count`）与 `build_correction` 的条目数提示同一轮加入
- 坑：system prompt 里的字面花括号必须转义（`{{"series": [...]}}`），否则 `SYSTEM_PROMPT.format()` 直接 `KeyError`
  —— **5.11 起改用 `render_system_prompt()`，转义已不需要**

### 5.6（2026-10-13，已上线）5 项体验/正确性修复 + AI 范文照抄护栏

① `MetricsView.vue` 数值变换 mul / add 同行（`.transform-row` 纯布局）；
② 报告任务收件人下拉可编辑回填（`ReportTaskRead` 增 `recipient_emails`，修掉了"编辑后覆盖原有收件人"的 bug）；
③ 去掉右上角重复页面名；④ 推送记录补记确认邮件 / 催办提醒（`_deliver_confirmation_mail` 成功与失败都写 `PushRecord`，
`action` 扩为 `push|retry_push|confirmation|reminder`，**序号语义变为"该报告邮件发送序号"**）；
⑤ AI 数字自检 `-09` 误报修复（`_mask_dates()`）。

服务器测试基线由此轮定为 609 passed。

---
### 5.7（2026-10-14，已上线，**浓缩版**）AI 请求留痕

诉求：要能查到每一次请求 AI 的具体参数与 prompt。做法 = 新表落库 + 界面可查。

- 迁移 **0015**：表 `ai_call_logs`（全新表，不动存量数据）
- 新增 `backend/app/models/ai_call.py`（kind / slot / period / actor_id / model / base_url / temperature / max_tokens / timeout / has_custom / `messages` / `response_text` / status / error / retried / retry_* / duration_ms）、`services/ai_trace.py`（`AiCallContext` / `AiCallRecord` / `sanitize_base_url` / `redact` / `safe_error` / `_prune`，每个主体只留最近 50 条）
- `api/ai_calls.py` + `schemas/ai_call.py`：`GET /api/ai-calls`（列表不含 messages / 正文）、`GET /api/ai-calls/{id}`
- 前端 `api/aiCalls.ts`、`components/AiCallDrawer.vue`（历史列表 + 参数区 + messages 分块着色 + 响应原文 + 重试对比）
- 红线：`base_url` 去 userinfo、`error` 脱敏，落库文案不得出现 `api_key` 字样
- 详情见 `DEPLOY.local.md` 的 `ai-trace-20261014` 记录

### 5.8（2026-10-14，已上线，**浓缩版**）文档与流程治理

无代码改动：为前几轮补齐 `AGENTS.md` 的需求小节（5.1-5.6）、在 `docs/` 留端到端冒烟文档、
并把"周报期次 + 曲线摘要"两轮合并上线（见 5.4 / 5.5 与 `DEPLOY.local.md` 的 `week-period-20261014`）。

### 5.9（2026-10-14，已上线，**浓缩版**）曲线摘要化 + 范文骨架条目上限

与 5.4 / 5.5 同批发布，核心是三件事：① 曲线只送 `series_stats` 摘要（见 5.5）；
② 规则 8 增加"范文的条目数就是输出的条目数"，并新增 `sample_item_overrun` 护栏；
③ 裸花括号转义（`{{"series": [...]}}`）以便继续用 `str.format()`。
本轮服务器基线 **680 passed**（备份 `deploy/backups/curve-summary-20261014/`）。

---
### 5.10（2026-10-14，**已发布上线**）：范文 = 输出模板（**废弃「整句复述」护栏**）

现场（模板「流量图」试跑留痕）：写作要求写的是「严格按参考范文，不要增加超出范文的其他输出」，
范文 `Gi0/0/21 上月平均 Bits received：161.11 Kbps`。模型第一次输出 `Gi0/0/21 上月平均 Bits received：262.47 Kbps`
—— **正是用户要的**（只替换数值与单位），却被判「整句复述范文」并触发纠正重试，
重试版写成 `Gi0/0/21 于 2026-09 的平均入向接收速率为 262.47 Kbps。`，反而偏离诉求。

- 根因是**一个错误前提**："范文只是风格样例，必须重新组织语言"。规则 8 与 `sample_copy_spans` 护栏都在实现它：
  - 规则 8 原文「范文只是结构、语气与措辞风格的样例，不是要转述或改写的内容；禁止逐句复述…必须重新组织语言」，
    与用户「写作要求」直接冲突（而规则 5/6 又说写作要求优先）
  - `sample_copy_spans`（与范文连续重合 ≥ 16 字即告警）命中即重试；`build_correction` 明说「不得复用范文的句子」
- 修复：
  - **删除 `sample_copy_spans`**（连同 `MIN_SAMPLE_COPY_SPAN`、`_COPY_SPAN_DISPLAY_CHARS`、`_NON_WORD_RE`）、
    返回体的 `sample_copy`、纠正文案的 copies 分支；`build_correction(leaks, overrun=0, sample_items=0)`
    （**第二参数已从 copies 变为 overrun**）；重试只剩「抄范文示例数字」「条目数超出范文」两类
  - 规则 8 重写：范文**就是本段输出的模板** —— 照它的句式、措辞、顺序写，只把数值与单位换成本期值；
    范文里的数字是**示例值**（禁抄、禁据此推算）；「本段写作要求若对改写法另有要求（例如换一种说法），以写作要求为准」；
    保留「范文的条目数就是输出的条目数」「范文没提到的指标一律不写」
  - user message 的范文标注、`InsightConfigDialog` 的 tooltip、`types.ts` 注释同步为「模板」语义；
    前端清理 `InsightConfigDialog` / `ReportDetailDialog` 的照抄告警、`types.ts` 的 `sample_copy` 字段、
    `AiCallDrawer` 的「护栏重试（命中范文示例数字 / 条目数超出范文）」
- **红线没松**（改动时别误删）：范文示例数字照抄 → `sample_leaks` 拦截并重试；凭空编造 → `verify_numbers`；
  超出范文条目 → `sample_item_overrun`。松掉的只有"复用范文句式"这一条**本来就该允许**的判定
- 测试：删 `TestSampleCopy`（5 个），新增 `TestSampleIsTemplate`（复用句式合法 / 示例数字仍被抓），
  `test_verbatim_copy_triggers_retry_and_uses_fix` 改写为 `test_reusing_sample_sentence_does_not_retry`
  （断言 **`route.call_count == 1`、`retried is False`、原文保留**）

**发布**：10 个文件（后端 6 + 前端 4），MD5 逐一核对一致；备份 `deploy/backups/sample-template-20261014/`；
`build backend frontend` → `up -d --force-recreate`；**无迁移**（head 仍 `0016`）；服务器 **677 passed**。

**真实留痕回归**：取那次被误判的调用（`ai_call_logs` 中 `retry_messages is not null` 的最新一条），
新护栏重算 → `leaks=[]`、`overrun=0` → **不再触发重试**（旧逻辑会把它改写成另一句）。

**踩坑**：自检脚本放 `/opt/reportpilot/backend/deploy-tmp/` 时**先 `mkdir -p` 再 `scp`**。

### 5.11（2026-10-15，**已发布上线**）：AI system 规则页面化

用户诉求：`ai_insight.SYSTEM_PROMPT` / `SYSTEM_PROMPT_USER_INPUT` 是模块常量，改一个字就得发版；要让管理员**在页面上随时改**。

取舍（用户确认）：生效范围 = **全局默认 + 模板可覆盖**；可编辑范围 = **全部规则自由编辑（1-8）**，不强制红线校验，只给「恢复默认」兜底；
配套 = **改完直接试跑验证** + **历史版本 / 谁改的 / 一键回滚**。未做：变量插入按钮（改为在气泡里写清两个占位符）、模板级历史版本、
独立的 `utils/aiPrompt.ts` 纯逻辑层（判断逻辑直接写在组件里，判据很少）。

**① 三段回退（不可破坏）**

- 顺序：绑定级 `ai_config_json.system_prompt / system_rules` → 全局 `app_settings.ai_system_prompt / ai_system_rules` → 代码常量
  `ai_insight.SYSTEM_PROMPT` / `SYSTEM_PROMPT_USER_INPUT`（**沿用原名，未改名**）
- 两段（基础规则 / 追加规则）**各自独立回退**：只有 `has_custom`（配了写作要求或参考范文）才追加第二段 —— 合并成一段会丢掉这个既有语义
- 唯一入口 `ai_insight.resolve_system_prompt(config, settings=None) -> (base, extra)`；
  **该函数必须定义在 `InsightConfig` 之后**（注解里的名字在定义时求值，3.11 立即求值 → 放前面会 NameError 起不来）
- `build_messages(..., *, settings=None)` 新增关键字参数，`generate_summary` 内部透传 `settings`
- 空白串（`"   "`）视为「未配置」→ 回落下一级（`_pick_text`），库里存了空白也不会让规则变空白

**② 占位符安全渲染（必须）**

- 旧实现用 `SYSTEM_PROMPT.format(threshold_pct=..., max_chars=...)`，要求正文里的 `{"series": [...]}` 写成 `{{...}}`；一旦开放编辑，裸花括号就抛 `KeyError`
- 改为**定点正则替换**：`render_system_prompt()` 只替换 `{threshold_pct}`（按 `f"{x:g}"`）与 `{max_chars}`，其余花括号**原样保留**；不再调用 `str.format()`
- 默认常量的 `{{...}}` 已还原为单花括号（该文本从未落库，无兼容负担）
- 回归锁：`test_ai_insight.py::TestBuildMessagesRendering::test_matches_legacy_format_output` —— 把常量按升级前的样子转义后再 `format()`，断言与新渲染结果**逐字相同**

**③ 全局文本零迁移**

- `Settings` 增 `ai_system_prompt` / `ai_system_rules`（默认空串）并加入 `runtime_config.EDITABLE_KEYS`（非密钥，明文存 `app_settings`），直接复用「保存 → 落库 → 刷新进程内缓存 → `effective_settings()`」，保存即生效、无需重启
- `runtime_config.reset(db, keys)` 新增：**真删行**（「恢复默认」必须删；`save` 传空串只是写个空值，那一行仍是"覆盖项"，判断会含糊）

**④ 审计与回滚（唯一的迁移：0017）**

- 新表 `ai_prompt_versions`（`models/ai_prompt.py` + 迁移 `20261014_0017_ai_prompt_versions.py`）：
  `actor_id / actor_name（登录名快照，防改名后失真）/ action(save|reset_default|rollback) / base_prompt / system_rules / replaced_from_id / previous_base_prompt / previous_system_rules / note / created_at`
- **append-only**：回滚 = 读历史文本后**再写一条** `rollback`，绝不改写已有行（`test_rollback_restores_text_and_appends_history` 断言三行依次为 `要保留的` / `后来改坏的` / `要保留的`）
- **不做 `_prune`**：规则文本改动频率极低，append-only 的账本价值高于省空间
- 写入口 `services/ai_prompt_service.py`：`current_texts` / `has_custom` / `record_change` / `save_prompt` / `reset_to_default` / `rollback_to` / `list_versions` / `get_version`

**⑤ 七个新端点（`api/system.py`）**

- `GET /api/system/settings/ai-prompt`（登录即可读，**同时下发 `default_base_prompt` / `default_system_rules`** 供前端"查看内置默认"，不在前端硬编码）
- `PUT 同路径`（`require_admin`，`max_length=4000` 校验在 Pydantic 层）
- `POST .../ai-prompt/reset`、`GET .../ai-prompt/versions`（列表**不含全文**，只给字数 + 160 字预览）、`GET .../ai-prompt/versions/{id}`（详情含全文）
- `POST .../ai-prompt/rollback`（body `{version_id}`，404 = 版本不存在）
- `POST .../ai-prompt/preview`：body `{template_id, placeholder, period, base_prompt, system_rules}`；复用 `compute_template_facts` + `collect_facts` + `generate_summary`（与模板页试跑**同一条取数路径**），写作要求/范文/字数/温度取该绑定**已存**配置（试真实上下文），两段 system 用页面文本经 `dataclasses.replace` 临时覆盖；不落库、不生成 docx，但**写 AI 留痕**（`kind="preview"`）；异常映射照抄模板页（422 / 502）
- `GET /api/system/settings` 增**一个**布尔 `ai_prompt_custom`（设置页卡片状态标签用）；两段文本**不是密钥**，完整回显
- 试跑端点**不做模板级权限校验**（`require_admin` 即可）：它是系统设置页的工具
**⑥ 绑定级字段（无迁移）**

- `schemas/template.py::InsightAIConfig` 增 `system_prompt` / `system_rules`（`max_length` 复用 `ai_insight.MAX_SYSTEM_*_CHARS`），`to_db()` 的空白清理键扩展到这两项；落库仍在 `template_bindings.ai_config_json`
- **加了 `model_config = ConfigDict(extra="forbid")`**：原先未知字段被静默丢弃，"前端传了却没落库"极难排查；现在拼错字段名直接 422
- `InsightConfig` 增两个字段 + `from_mapping` 用新增的 `_clip(raw, limit)`（非字符串 → `str()`，去空白，截断到 4000）
- 报告生成 / 单段重生成 / 绑定保存**无需另改**（都走 `from_mapping` / `to_db`；新建版本是整字典复制，重生成是泛化 dict 合并）

**⑦ 前端**

- 新增 `components/AiSystemPromptDialog.vue`（两段等宽 textarea + 字数统计 + 恢复默认 + 查看内置默认 + 未保存/已自定义状态标签 + 分隔线以下的试跑区）
- 新增 `components/AiPromptHistoryDrawer.vue`（`el-timeline`，动作标签 + 操作人 + 时间 + 字数 + 预览，点开看全文，逐项「回滚到此版本」，最新一条标「当前」）
- `SystemSettingsView.vue`：AI 卡片（`cfg-card-ai`）增「系统提示词」行 —— 状态标签 + 「编辑规则」「历史」，保存后 `@saved="loadSettings"` 刷新卡片
- `InsightConfigDialog.vue`：增折叠区「本段系统提示词（高级，留空 = 用全局默认）」+「清除本段覆盖」按钮
- `api/system.ts`：增 7 个方法 + 相关类型；`api/types.ts`：`InsightAIConfig` 增两个字段

**本机验证**：`test_ai_prompt_api.py` 18 passed + `test_ai_insight.py` 83 passed；
扩面回归 `test_ai_prompt_api / test_ai_insight / test_system_settings_api / test_system_settings_write / test_insight_custom_api` 125 passed；
前端 typecheck + 199 vitest + build 全绿。

**发布**：18 个文件（后端 12 + 前端 6），MD5 逐一核对一致；备份 `/opt/reportpilot/deploy/backups/ai-prompt-page-20261015/src-before.tar.gz`（335KB）；
`alembic upgrade head`（**0016 → 0017**）→ `build backend frontend` → `up -d --force-recreate backend frontend`。
验证：4 容器 Up、`alembic current` = `0017 (head)`、新表 12 列齐全、七端点未认证 401、Web 200、
前端产物 `SystemSettingsView-DYMVtvi6.js` 含 `ai-prompt-dialog` / `cfg-edit-prompt` / `ai-prompt-preview-run`，
`TemplatesView-DvJ1yFLI.js` 含 `insight-cfg-system-prompt`，产物无 `sample_copy`；**服务器裸容器全量 711 passed**（677 + 34）。

**两次线上事故（都已修复，教训值得记住）**

1. **后端 502：`NameError: name 'InsightConfig' is not defined`** —— 函数定义在 dataclass 之前，注解在定义时求值。
   本机 Python **3.14**（PEP 649 延迟求值）不报错，生产镜像 **3.11** 立即求值 → 整个后端起不来。已把函数移到 dataclass 之后。
2. **全量测试 order-dependent 失败（本地全绿、服务器 3 红）** —— `get_settings()` 是 `@lru_cache` 单例，
   测试里直接 `ai_settings.ai_system_prompt = "..."` 污染整个 pytest 进程；本地之所以没暴露，是我把两个文件的执行顺序写反了。
   已改用 `monkeypatch.setattr`；**验收必须按字母序全量跑**。

**⚠ 本轮另一重大发现：本地工作区与服务器源码双向分叉（已处理）**

- 本地 `backend/` 有 28 个文件被回退到更早状态（`models/report.py` 本地 106 行 / 服务器 167 行且带 `source`，
  而本地测试在用 `source=` → 本地自相矛盾）；`app/services/adapters/__init__.py` 整个缺失（只剩 `.pyc`）
- 前端同样分叉：服务器 `SystemSettingsView.vue` 785 行（含已上线验收的邮件/AI 编辑弹窗），本地 535 行（没有）；
  本地多一个**从未部署且未写完**的 `views/ReportsView.vue`（852 行，引用服务器侧不存在的 `reportsApi.create`）
- 处理（用户确认"以服务器为准"）：本地 `frontend/src` 整树存到服务器 `deploy/backups/frontend-local-unreleased-20261015.tar.gz`（113KB）
  → 服务器版本覆盖本地 → `ReportsView.vue` 移到 `frontend/unreleased/`（保留但**不参与构建**，否则 typecheck 报 7 个错）
- **教训**：部署前必须**全树逐文件核对 MD5**（backend + frontend 都算）；本地测试通过 ≠ 本地源码是新的
**UI 验收（2026-10-15 已完成，ops 账号 + playwright-cli firefox）**

| # | 项 | 结果 |
|---|---|---|
| 1 | AI 卡片出现「系统提示词」行，标签「内置默认」+「编辑规则」「历史」 | 通过：`cfg-tag-prompt` / `cfg-edit-prompt` / `cfg-history-prompt` 均在 |
| 2 | 编辑器两段预填空；「查看内置默认」读到规则原文 | 通过：`baseLen=0 rulesLen=0`；预览 2 块，含规则 1 与 `series` 片段 |
| 3 | 写入含 `{max_chars}` 与裸花括号 `{oops}` / `{'series': [...]}` 的文本 → 保存 | 通过：二次确认正常；标签变「已自定义」；回读一致（180 字）；**裸花括号原样入库** |
| 4 | 选模板 + 槽位 `summary` + 周期 2026-08 → 试跑出稿 | **被 AI 服务端超时阻塞**（见下）。UI 侧全正常：模板/槽位联动、请求到达后端并算完 facts（曲线 1488 点 → Mbps）、留痕落 3 条 `degraded` |
| 5 | 恢复默认 → 标签回「内置默认」；历史列表 | 通过：值清空、`ai_prompt_custom=false`、标签回位；历史 2 条（恢复默认 #2 标「当前」、保存 #1 带 180 字预览 + 操作人 ops） |
| 6 | 回滚到 #1 → 文本恢复；历史 append-only | 通过：恢复 180 字；历史变为 `1:save` / `2:reset_default` / `3:rollback(from 1)`，旧行未被改写 |
| 7 | 绑定弹窗折叠区「本段系统提示词（高级）」 | 通过：默认收起、标题正确、两个 textarea 齐全、填值后出现「清除本段覆盖」；`PUT bindings` 往返正常 |

**阻塞项：DeepSeek 从今天 11:38 起不再返回响应体（与本次改动无关）**

- `ai_call_logs` 今天早些时候（id 6/7）同样调用 **2 秒 success**；11:38 之后（id 8/9/10）全部 `degraded` +
  `APITimeoutError: Request timed out`，`duration_ms` 约 **361 秒**（= 3 次重试 x `ai_timeout_seconds=60`）
- 后端日志模式：`POST https://api.deepseek.com/chat/completions "200 OK"`（只到响应头）→ 60s 后 `Retrying request` → 3 次后降级
- **对照组证明不是回归**：未改动的模板页端点 `POST /api/templates/1/insight-preview` 用同一份 facts 也同样超时
- 连带 UX 缺口（建议后续处理）：后端降级要 361 秒才返回，**超过 nginx 读超时** → 浏览器先收到 504，
  用户只看到一个转瞬即逝的 toast，**看不到「AI 不可用已降级」告警卡片**。
  可选修：`ai_timeout_seconds` 调小 / 关掉 openai 客户端重试 / 前端对 504 明确提示 / 网关读超时与后端最坏耗时对齐

**验收期间的一次误操作（已修复，生产零残留）**

- 点「AI 摘要配置 → 确定」时点错按钮层级，导致外层绑定弹窗以空表单提交，**把 `summary` 槽位的 `prompt` 与 `sample_text` 清掉了**
- 修复：`PUT /api/templates/1/bindings` 按原值写回（原文逐字恢复，临时加的 `system_prompt` 一并移除）
- 事后核对：`app_settings` 里两行提示词覆盖 = **0**（全局回到内置默认）；`template_bindings` 中 `version_id=14` 的 `summary`
  = `{prompt: "只总结和替换Gi0/0/21的相关指标…", sample_text: "Gi0/0/21 上月平均 Bits received：161.11 Kbps"}`；
  `ai_prompt_versions` 保留 4 条（`1:save` / `2:reset_default` / `3:rollback` / `4:reset_default`）
- 教训：**绑定是「全量替换」语义**，页面上任何一次保存都会提交整张表单；验收前先 `GET /api/templates/1` 存原始 bindings 快照

**新增踩坑（playwright 验收 + 工具链）**

1. Element Plus `el-select` 的选中项在 `.el-select__selected-item` 文本里，**不在 `input.value`**（`filterable` 时 input 只是筛选框）
2. 下拉弹层是 `position: fixed`（teleport 到 body），**`offsetParent` 恒为 null**；判断可见用 `getClientRects().length` 或 Playwright 的 `:visible`
3. 内层弹窗按钮按 `data-testid` 点（`insight-cfg-open` → `insight-cfg-save` → 外层 `bind-save`），按文案匹配容易点错层级
4. `PeriodPicker` 粒度是 radio（`input.value === 'month'`），期间在 `.el-input__inner`；`el-date-picker` 不透传 `data-testid`
5. token 存 `localStorage.rp_token`
6. **`write_to_file` 对超过约 8KB 的内容会报「missing required argument filePath」（误报）**；大文件改用 PowerShell here-string 分块写
7. **`exec_command` 单条命令上限 8000 字节**；多行远程脚本要放 `.ps1` 并去掉 CR（否则 bash 报 `$'...do\r'`）
8. **不要用 `List[string].AddRange()` 拼接文件行**：`ReadAllLines` 返回 `Object[]`，AddRange 抛类型转换异常，
   失败后 `WriteAllLines` 会把文件写成残缺 —— **本轮就是这样把 AGENTS.md 截断的**。追加内容只用 `AppendAllText`
9. **PowerShell 读写文件必须显式 UTF-8**：`Get-Content` / `Get-Content -Raw` 默认按 ANSI(GBK) 读 UTF-8 文件，
   追加回写会把中文变成乱码（本轮踩过）。用 `[System.IO.File]::ReadAllText/ReadAllLines/AppendAllText(..., UTF8)`

---
## 6. 部署（概要）

服务器：`root@172.20.163.xxx:PORT_REDACTED`（密钥 `REDACTED_KEYNAME`），项目目录 `/opt/reportpilot`。
**没有 git**，靠上传覆盖；覆盖前源码备份到 `/opt/reportpilot/deploy/backups/<日期-轮次>/`。
连接参数与外部服务（Zabbix / DeepSeek / SMTP / ONLYOFFICE）见 `DEPLOY.local.md`。

标准流程：

```
cd /opt/reportpilot
# 1) 备份 -> 2) scp 上传（逐文件核对 MD5） -> 3) 有迁移才 upgrade
docker compose build backend frontend
docker compose run --rm -T backend alembic upgrade head     # 仅在有新迁移时
docker compose up -d --force-recreate backend frontend
# 4) 验证：迁移版本、容器状态、日志、端点状态码、前端产物
docker compose exec -T backend alembic current
docker compose ps && docker compose logs backend --tail 12
```

**跑测试只能用裸容器**（勿用 `docker compose run backend pytest`：会被 `env_file: [.env]` 注入生产
`ONLYOFFICE_JWT_SECRET`，`test_template_versions_acl_api.py` 5 个回调用例会假失败）：

```
docker run --rm -v /opt/reportpilot/backend:/code -w /code reportpilot/backend:latest \
  python -m pytest -p no:cacheprovider -q          # 基线 711 passed
```

端口：后端 `8000`（**未映射到宿主**，只能从容器内或经 nginx `:8080/api` 访问）；生产前端 nginx `8080`；
开发前端 vite `5173`；DB `5432`。反向代理 / uvicorn 单请求超时响应 **约 5 分钟**（报告同步生成）。

**回滚**：解包对应 `deploy/backups/<目录>/src-before.tar.gz` 覆盖回 `/opt/reportpilot` → 重建镜像 →
若有迁移则 `alembic downgrade <rev>` → 重建容器。

**发布记录**（每轮细节见 `DEPLOY.local.md` 对应小节）：
`week-period-20261014`（周报期次 + 曲线摘要，迁移 0016）、`sample-template-20261014`（范文=模板，废弃整句复述护栏）、
`ai-prompt-page-20261015`（system 规则页面化，迁移 0017）。