# ReportPilot 端到端 Smoke Test 执行报告（真实环境）

日期：2026-09-30 · 环境：Windows 11 + Docker Desktop（Compose v5.5.1）  
被测对象：`docs/smoke_test.md` 全 13 步 + 附录日志自查

结论：**链路全通**。真实 PG 16 + 真实 Zabbix 7.2.0 + 真实 DeepSeek + 真实 exmail SMTP  
全部打通，"数据源 → 指标 → 模板 → 预览 → 报告 → 审核 → 推送"完整闭环走通。  
过程中发现并修复 **4 个真实缺陷**（1 个阻断启动、3 个正确性/健壮性），修复后  
**单元测试 174/174 全绿**，数值经独立验算逐位吻合，日志与 DB 无任何敏感信息泄漏。

---

## 一、测试参数

| 项      | 值                                                                                                                                  |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| Zabbix | `https://zb.livecom.hk`（自签名证书，`verify_ssl=false`），版本 7.2.0                                                                         |
| 主机组    | `174` = 爱迅达办公室                                                                                                                     |
| 主机     | `12793` = 172.20.192.253（测试华为S5735）                                                                                                |
| 监控项    | `261006` = Gi0/0/21 Bits received（`net.if.in[ifHCInOctets.26]`）<br />`261108` = Gi0/0/21 Bits sent（`net.if.out[ifHCOutOctets.26]`） |
| AI     | `https://api.deepseek.com`，实际模型 ID `deepseek-flash`                                                                                |
| SMTP   | `smtp.exmail.qq.com:465`，`aiops@livecom.hk`                                                                                        |
| 统计周期   | 2026-08（本期）/ 2026-07（基线）                                                                                                           |

> 选 2026-08 而非 9 月的原因：9 月在该 Zabbix 上**无 trend 数据**（`ret 365d` 但未落桶），  
> 取数会命中"无趋势数据"。8 月与 7 月均有完整小时级 trend，适合做环比。  
> （另：该 Zabbix 的 `now` 为 UTC，当前约 2026-09-30 02:5x，故 8 月为完整历史月。）

---

## 二、逐步结果

| 步  | 项              | 结果 | 证据                                                                                                       |
| -- | -------------- | -- | -------------------------------------------------------------------------------------------------------- |
| 0  | .env 配置        | 通过 | ENCRYPTION_KEY 复用既有 key；AI/SMTP/Zabbix 按实际填写                                                             |
| 1  | 启动服务           | 通过 | `{"status":"ok"}` HTTP 200；db healthy / backend running                                                  |
| 2  | 迁移 0001→0004   | 通过 | `Running upgrade 0001 -> 0002 -> 0003 -> 0004`                                                           |
| 2  | 7 张业务表         | 通过 | users / data_sources / metric_definitions / word_templates / template_bindings / reports / report_audits |
| 3  | seed 用户        | 通过 | id=1 admin                                                                                               |
| 4  | 创建数据源          | 通过 | **201**，`secret_hint:"****3a91"`，无明文；DB `secret_encrypted` 184 字符密文                                      |
| 5  | 连通性测试          | 通过 | `{"success":true,"message":"连接成功"}`                                                                      |
| 5  | 三类错误可区分        | 通过 | 见下表                                                                                                      |
| 6  | 创建指标           | 通过 | **201**，含 id/query_config/calc_formula/is_active                                                         |
| 6  | 三种校验错误         | 通过 | 422 数据源不存在 / 409 名称重复 / 422 lookback 必填                                                                  |
| 7  | 试算并核对          | 通过 | 数值**逐位吻合**独立手算，见 §三                                                                                      |
| 7  | 两种边界错误         | 通过 | 422 item 未在组中找到 / 502 不可达                                                                                |
| 8  | 上传正常模板         | 通过 | **201**，`placeholders:["gi_in","gi_out","summary"]`（`gi_in` 跨 run 拆分仍识别）                                 |
| 8  | 上传违规模板         | 通过 | **422**，`violations` 列出 `{% if %}` 与 `{{ a.b }}`                                                         |
| 9  | 绑定变量           | 通过 | `cleared_count:0`；insight `binding_key` 自动为 `summary`；`unbound:[]`                                       |
| 10 | 预览             | 通过 | `gi_in→231421.96`、`gi_out→276170408.00`、`summary→[AI摘要待生成]`；**原模板未被修改**                                  |
| 11 | 生成报告           | 通过 | **201**，`status:"generated"`，收件人去重                                                                       |
| 11 | facts/insights | 通过 | facts 含 value/unit/source/object/formula/period 全字段；`degraded:false`，真实 AI 文本                            |
| 12 | 审核流转           | 通过 | `generated → pending_review → approved`；audits 逐条增长                                                      |
| 12 | 非法流转           | 通过 | `generated` 直接 approve → **409**                                                                         |
| 13 | 推送             | 通过 | `status:"pushed"`，`pushed_at` 非空                                                                         |
| 13 | 邮件收件           | 通过 | 主题 `【ReportPilot】运维月报模板 2026-08 月报`，附件 `运维月报模板_2026-08.docx`                                             |
| 13 | 附件一致性          | 通过 | 附件 md5 `ea6aa555…` **与 API 产物逐字节一致**                                                                     |
| 13 | 幂等             | 通过 | 重复 `/push` → **409**                                                                                     |
| 附  | 日志敏感信息         | 通过 | backend 591 行 + db 76 行，11 项检查全 PASS                                                                     |

### 5 步三类错误区分

| 构造                           | 实测 message                               |
| ---------------------------- | ---------------------------------------- |
| URL 指向不可达地址                  | `URL 不可达: Zabbix 地址不可达或超时: ConnectError` |
| URL 正确 + 错误 token            | `认证失败: API token 无效或已过期`                 |
| `-32602` + `No permissions…` | `权限不足: <data>`（分类逻辑经构造验证）                |

> 说明：本 token 为超管，无法在线上真实触发"权限不足"。该分支通过复刻 `_call`  
> 分类逻辑 + 构造响应体验证；同时确认异常消息**不含 token 明文**。

### 数据源脱敏

- 接口出参只有 `secret_hint: "****3a91"`，无明文
- DB 存 Fernet 密文（184 字符，`gAAAAAB…` 开头）
- `pg_dump` 全库 grep `ce0afce|Livec0m|sk-7baec9|-rMpkKOag` → **0 命中**

---

## 三、数值独立验算（关键检查点）

不依赖被测代码，直接用 `trend.get` 拉原始小时级数据，按 calc_engine 文档口径手算：

| 口径                           | 手算                   | API 返回               | 一致 |
| ---------------------------- | -------------------- | -------------------- | -- |
| 2026-08 `avg(value_avg)`（本期） | `231421.95833333334` | `231421.95833333334` | ✅  |
| 2026-07 `avg(value_avg)`（基线） | `235963.08389715833` | `235963.08389715833` | ✅  |
| `change_pct`                 | `-1.924506786749821` | `-1.924506786749821` | ✅  |
| 2026-08 `max(value_max)`（峰值） | `276170408.0`        | `276170408.0`        | ✅  |
| 2026-07 `max(value_max)`（基线） | `218156600.0`        | `218156600.0`        | ✅  |

抽样小时点（供 Zabbix 前端核对）：  
`2026-08-01T00:00Z min=5840 avg=7225 max=34632 num=60`；  
`2026-08-16T12:00Z min=0 avg=3534 max=21360 num=38`。

> **注**：上表为**修复后**的最终数值。修复前因边界缺陷（见 §四·D1）值分别为  
> `231450.1476510067` / `235653.97837837838`（偏差 +28.19 bps）。

### AI 摘要数字溯源

最终 AI 摘要（134 字，`degraded:false`）：

> 本期 gi0_0_21_inbound_avg 为 231421.96 bps，环比 -1.92%，波动平稳。  
> gi0_0_21_outbound_peak 达 276170408 bps，环比 +26.59%，波动显著，  
> 出向峰值明显抬升，需关注带宽峰值压力与扩容余量。

逐数字比对 `facts_json`：`231421.96` / `-1.92%` / `276170408` / `+26.59%` **全部可溯源**。  
`unverified_numbers` 仅剩 `['0','21']`，来自指标名 `gi0_0_21` 被正则切碎的片段，**非编造**。

---

## 四、发现并修复的 4 个真实缺陷

### D1（阻断启动）容器镜像缺 `python-dateutil`

`docker compose up` 后 backend 反复退出：`ModuleNotFoundError: No module named 'dateutil'`。  
`requirements.txt` 已声明 `python-dateutil`，但既有镜像构建于声明之前，未重建。  
**修复**：`docker compose build backend`。

### D2（正确性，偏差 +28.19 bps）`trend.get` 时间区间末端多算 1 小时

`period_to_unix_range` 返回 `[首日00:00, 次月首日00:00)`，但 Zabbix trend 的小时桶  
以「桶起始时刻」为 `clock`（桶覆盖 `[clock, clock+3600)`）。当桶边界恰与 `time_till`  
重合时，**属于次月的那一桶被计入本期**。

实测 2026-08 的 745 条 trend 中，末条 `clock=1788220800 = 2026-09-01T00:00:00Z`  
（9 月数据，该小时 `avg=252423`、`max=2407456`）。剔除后：

```
含越界桶 n=745 avg=231450.1476510067
剔除越界桶 n=744 avg=231421.95833333334   偏差 28.19 bps
```

**修复**：在 `ZabbixAdapter.fetch_trend_data` 内把 `time_till` 回退 1 秒  
（`max(time_from, time_till - 1)`），使区间严格等价于 `[start, end)`。  
放在 adapter 层而非 `period_utils`，因为这是 Zabbix 桶语义的适配问题，且  
`period_utils` 的既有语义已被 17 个单测固化，改 adapter 层**零测试改动**。

### D3（健壮性，500）`item_ids` 含空串触发 500 而非 422

`resolve_items` 里 `int(i)` 遇到空串抛 `ValueError` → 未捕获 → HTTP 500。

**修复**：`calc_engine.resolve_items` 先 strip + 过滤空值，非数字项显式抛  
`MetricComputeError`（→ 422）。

> 附带发现：若 `item_ids` 与 `item_key_pattern` 都为空，会"聚合全组 440 个 item"，  
> 进而触发 Zabbix `trend.get` **HTTP 500**（查询规模上限）。该路径已由 D3 的过滤  
> 收敛到"未限定范围"告警分支。**建议 Phase 2 显式拒绝未限定范围的指标定义**——  
> 本次未改，因既有单测将"未限定即聚合全组"作为预期行为固化。

### D4（正确性，误报）`verify_numbers` 因符号不匹配误报

中文行文"下降 1.92%"对应 `change_pct = -1.9245…`，数字不带负号。  
`verify_numbers` 剥离 `%` 后用 `1.92` 与 `-1.9245` 比对，差值 `3.8445 > 0.005`，  
**把正确表述误判为未验证数字**。

**修复**：`ai_insight.verify_numbers` 对负值候选额外加入其绝对值参与比对。  
验证：`"环比下降 1.92%"` → `[]`；`"环比 -1.92%"` → `[]`；  
真正编造的 `"环比上升 5.55%"` → `['5.55%']`（仍能抓住）。

---

## 五、测试与安全检查

### 单元测试

```
174 passed, 9 warnings
```

修复前基线同为 **174 passed**（`period_utils` 保持原语义、adapter 层修复零测试改动）。  
过程中曾把边界修复放在 `period_utils` 层，导致 17 个测试失败——那些测试断言  
旧边界值且 `FakeAdapter` 以 `(time_from, time_till)` 元组为 key，属测试与实现紧耦合。  
改到 adapter 层后全部通过。

### 日志敏感信息自查（11 项全 PASS）

| 检查项                           | backend.log | db.log |
| ----------------------------- | ----------- | ------ |
| Zabbix token 完整值              | PASS        | PASS   |
| token 末4位以外子串                 | PASS        | PASS   |
| token 中段                      | PASS        | PASS   |
| `SMTP_PASSWORD`               | PASS        | PASS   |
| `ENCRYPTION_KEY`              | PASS        | PASS   |
| `AI_API_KEY`（完整与前段）           | PASS        | PASS   |
| `SMTP_USERNAME`               | PASS        | PASS   |
| `Authorization` / `Bearer` 字样 | PASS        | PASS   |

关键操作日志齐全：数据源创建/更新、连通性测试、指标创建/计算、报告生成、  
AI 摘要、状态流转、邮件发送、推送成功——全部可追溯。  
连通性失败时日志仅含方法名/状态码/异常类型，**无请求头与 body**。

---

## 六、遗留事项与建议

1. **未限定范围的指标定义建议在创建时拒绝**（D3 附带发现）。当前允许  
   `item_ids` 与 `item_key_pattern` 都为空 → 聚合全组，易触发 Zabbix `trend.get` 500  
   且口径不可审计。本次因既有单测固化了该行为未改。
2. **存量数据未回溯**。D2 修复只影响修复后的取数；修复前生成的报告 1 值偏  
   +28.19 bps，建议按新口径重算。
3. **自签名证书**仍以 `verify_ssl=false` 绕过，生产前应把站点 CA 挂进容器。
4. **`db.log` 含查询字符串**（Zabbix 的 500 报错），虽不含敏感值，  
   生产建议收紧 PG 日志级别。
5. `.env` 中 `AI_MODEL` 用的是**模型 ID** `deepseek-flash`（而非展示名  
   `DeepSeek-V4.1-Flash`）——该端点只接受 ID，传展示名会 400。已在 `.env` 注明。

## 七、约束遵守

- volume `backend_pgdata` **未删除**，历史数据（id=1/2 旧数据源）保留
- `docker-compose.yml` 端口映射**未改动**
- `.env` 未提交；全程未执行任何 git 写操作
- 改动仅限 3 个源文件：`zabbix_adapter.py`、`calc_engine.py`、`ai_insight.py`
