/** 与后端 Pydantic schema 对齐的 TS 类型（手写维护，字段变更需同步） */

export type UserRole = 'admin' | 'user'

export interface UserRead {
  id: number
  username: string
  email: string
  display_name: string
  role: UserRole
  source: string
  /** 停用后不可登录；用户管理页展示状态 */
  is_active?: boolean
}

export interface LoginRequest {
  username: string
  password: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  user: UserRead
}

/* ---------- 数据源 ---------- */

export interface DataSourceConfig {
  url: string
  /**
   * Zabbix 专有：JSON-RPC 的鉴权方式。AIOps 走 HTTP header `token`，
   * 不带这个字段（实测不带 token 会返回 code=9 auth failed）。
   */
  auth_type?: 'bearer'
  timeout: number
  verify_ssl: boolean
}

export interface DataSourceRead {
  id: number
  name: string
  type: string
  config: DataSourceConfig
  is_active: boolean
  created_at: string
  secret_hint: string
}

export interface DataSourceCreate {
  name: string
  type: string
  config: DataSourceConfig
  secret: string
}

export interface DataSourceUpdate {
  name?: string
  config?: DataSourceConfig
  secret?: string
  is_active?: boolean
}

export interface ConnectionTestResult {
  success: boolean
  message: string
}

/* ---------- 指标 ---------- */

export type AggType = 'sum' | 'avg' | 'max' | 'min'
export type CompareType = 'none' | 'mom' | 'yoy'

export interface QueryConfig {
  /** Zabbix 主机组（与 host_ids 成对出现） */
  host_group_ids: string[]
  /** Zabbix 主机（须属于 host_group_ids） */
  host_ids: string[]
  /** AIOps 模型（与 instance_ids 成对出现；语义对应「主机组」）。Zabbix 指标不传。 */
  model_ids?: string[]
  /** AIOps 实例（须属于 model_ids；语义对应「主机」）。Zabbix 指标不传。 */
  instance_ids?: string[]
  /**
   * 取数对象：scalar 多选 / pie 严格 1 个 / text 0~1 个（mode=fixed 可空）
   *
   * Zabbix 存纯数字 item_id；AIOps 存**定位串**（见 `utils/aiopsRef.ts`）：
   * `n:<实例>:<监控项>` 或 `l:<实例>:<监控项>:<列>:<b64url(主键行)>`。
   */
  item_ids: string[]
  /** curve 专用：每条曲线独立指定 item 与展示名 */
  series?: CurveSeriesConfig[]
  value_type_override?: number[] | null
}

export type MetricType = 'scalar' | 'curve' | 'pie' | 'text' | 'time'

/** MetricFact.value 的形态，消费端据此解析 */
export type ValueShape = 'scalar' | 'series' | 'pie' | 'text'

export interface Transform {
  /** value = raw * mul + add */
  mul: number
  add: number
}

export interface CurveSeriesConfig {
  item_id: string
  display_name: string
  transform?: Transform
}

export interface PieConfig {
  up_values: string[]
  down_values: string[]
  unknown_mode: 'exclude' | 'error'
}

export interface TextConfig {
  mode: 'latest' | 'fixed'
  fixed_value: string | null
}

/** 快速偏移：按计算时刻解析为具体日期（与后端 TIME_QUICK_OFFSETS 一致） */
export type TimeQuickOffset =
  | 'last_week_monday'
  | 'last_week_sunday'
  | 'last_month_first'
  | 'last_month_end'

export interface TimeConfig {
  /** 有序格式模块：字段 年/月/日/时/分/秒，字面量 空格/-/ /:/:/: */
  format_modules: string[]
  /** 月/日/时/分/秒是否补零（年始终 4 位） */
  zero_pad: boolean
  /** 偏移方式：manual=手动偏移 / quick=快速偏移（二选一） */
  offset_mode: 'manual' | 'quick'
  /** manual 模式：平移量，正数向后、负数向前 */
  offset_value: number
  offset_unit: 'day' | 'month'
  /** quick 模式：按计算时刻解析的具体日期 */
  quick_offset: TimeQuickOffset | null
}

export interface TypeConfig {
  /** scalar */
  transform?: Transform
  /** curve */
  transform_default?: Transform
  max_series?: number
  /** pie */
  pie_config?: PieConfig
  /** time */
  time_config?: TimeConfig
  /** text */
  text_config?: TextConfig
}

export interface CalcFormula {
  agg: AggType
  compare: CompareType
  baseline_lookback?: number | null
}

/**
 * 单位换算（对齐后端 schemas.metric.UnitScaleConfig，仅 scalar / curve 生效）。
 *
 * 与「数值变换」type_config.transform 的分工：transform 改的是**事实层数值**
 * （参与环比与阈值判定）；unit_config 只改展示口径，不参与任何计算。
 */
export interface UnitScaleConfig {
  /**
   * none: 不换算；auto: 按量级自动选 K/M/G/T；fixed: 固定除数。
   *
   * 注意：`fixed` 只保留给存量数据读取（后端 `unit_scale.py` 仍兼容）；
   * 指标编辑页的「单位」行只产出 `none` / `auto`（勾选换算 + 进制）。
   */
  mode: 'none' | 'auto' | 'fixed'
  /** auto 的进制（缺省 1000） */
  base?: 1000 | 1024
  /** auto 的单位后缀，留空回退指标 unit（"bps" -> Kbps/Mbps/Gbps） */
  suffix_unit?: string
  /** fixed 的除数（必须 > 0） */
  divisor?: number
  /** fixed 的目标单位 */
  display_unit?: string
  /** 展示小数位（留空：用指标 display_precision，再缺省 2 位） */
  decimals?: number | null
}

export interface MetricDefinitionRead {
  id: number
  name: string
  data_source_id: number
  metric_type: MetricType
  query_config: QueryConfig
  type_config: TypeConfig
  calc_formula: CalcFormula
  unit: string
  period_type: string
  display_precision: number | null
  tags: string[]
  /** null / {"mode":"none"} = 不换算 */
  unit_config?: UnitScaleConfig | null
  is_active: boolean
  created_at: string
}

/** 详情接口附带的 id -> name 解析结果（数据源不可达时为 null） */
export interface ResolvedRef {
  id: string
  name: string
}

export interface QueryConfigResolved {
  host_groups: ResolvedRef[]
  hosts: ResolvedRef[]
  items: (ResolvedRef & { host_id: string; host: string })[]
}

export interface MetricListItem extends MetricDefinitionRead {
  query_config_resolved: QueryConfigResolved | null
}

export interface MetricDetail extends MetricDefinitionRead {
  query_config_resolved: QueryConfigResolved | null
}

export interface MetricDefinitionCreate {
  metric_type: MetricType
  name: string
  /** time 类型不取数 -> 传 null；其余类型必填（后端会校验） */
  data_source_id: number | null
  query_config: QueryConfig
  type_config?: TypeConfig
  /** 仅 scalar 使用 */
  calc_formula?: CalcFormula
  unit: string
  period_type: string
  display_precision?: number | null
  tags?: string[]
  unit_config?: UnitScaleConfig | null
}

export interface MetricDefinitionUpdate {
  name?: string
  data_source_id?: number | null
  metric_type?: MetricType
  query_config?: QueryConfig
  type_config?: TypeConfig
  calc_formula?: CalcFormula
  unit?: string
  period_type?: string
  display_precision?: number | null
  tags?: string[]
  /** 传 {"mode":"none"} 关闭换算；不传 = 本次不修改 */
  unit_config?: UnitScaleConfig | null
}

/* ---------- 取数范围下拉（三级级联）---------- */

export interface HostGroupRef {
  group_id: string
  name: string
  /** AIOps：该模型下的实例数（用于下拉里的提示）；Zabbix 不返回 */
  instance_num?: number | null
  /** AIOps：该模型的模板数；Zabbix 不返回 */
  template_num?: number | null
}

export interface HostRef {
  host_id: string
  name: string
  groups: HostGroupRef[]
  /** AIOps：实例 IP（实测 show_attr.ip 恒为字符串）；Zabbix 不返回 */
  ip?: string | null
}

/**
 * AIOps list 型监控项的一列。
 *
 * 由 `item/list` 的 `source_*` 平行数组解出，**不需要额外请求**。
 * `has_history` 是**列级能力标志**，不保证每一行都有数据。
 */
export interface AIOpsColumn {
  column_id: number
  name: string
  unit: string
  value_type: 'number' | 'text'
  has_history: boolean
  is_primary: boolean
}

/** AIOps list 型监控项的一个主键行（只返回该列当前值非空的行）。 */
export interface ItemRowRef {
  row_key: string
  current_value: string
  as_of: number
}

export interface ItemRef {
  item_id: string
  name: string
  key: string
  host_id: string
  host: string
  value_type: number
  units: string
  /* ---- AIOps 扩展：Zabbix 下均为 undefined，后端已用 exclude_none 保证不出现 ---- */
  /** 监控项级类型，决定是否要展开「列 -> 主键行」两级 */
  aiops_data_type?: 'number' | 'text' | 'list'
  /** list 型才有的列元数据；number / text 型为空 */
  aiops_columns?: AIOpsColumn[]
  aiops_template_id?: number | null
  aiops_template_name?: string | null
  /** 采样周期（秒），实测普遍为 60 */
  aiops_cycle?: number | null
}

/** curve 的一条曲线（后端 CurveFact） */
export interface CurveSeriesPayload {
  name: string
  item_id?: string
  points: [number, number][]
}

export interface SeriesPoint {
  name: string
  points: [number, number][]
}

/** pie 计算结果（后端 PieFact） */
export interface PieValue {
  up_count: number
  down_count: number
  up_pct: number
  down_pct: number
}

/** pie 适用性判定（suitable=true 适合；false 不适合；'unknown' 超时/数据量大需人工确认） */
export type Suitability = boolean | 'unknown'
export type SuitabilityConfidence = 'high' | 'medium' | 'low' | 'unknown'

export interface PieSuitability {
  suitable: Suitability
  confidence: SuitabilityConfidence
  reason: string
  distinct_values: string[]
  suggested_config: { up_values: string[]; down_values: string[] } | null
  has_valuemap: boolean
  valuemap: Record<string, string>
}

export interface PieSuitabilityRequest {
  host_group_ids: string[]
  host_ids: string[]
  item_id: string
}

export interface MetricFact {
  metric_id: number
  name: string
  /** scalar: number；series/pie/text: 结构化对象（由 value_shape 决定） */
  value: number | Record<string, unknown>
  /** 缺省视为 scalar（兼容历史 facts_json） */
  value_shape?: ValueShape
  baseline_value: number | null
  change_pct: number | null
  unit: string
  source: string
  object: string
  formula: string
  period: string
  /** 该指标的小数精度（来自 MetricDefinition.display_precision）；旧报告可能缺省，前端回退原始值 */
  display_precision?: number | null
  /* ---- 展示派生层（由指标的 unit_config 决定，后端 calc_engine 计算）---- */
  /** 换算后的展示值；null/缺省 = 未配置换算，回退 value */
  display_value?: number | null
  /** 换算后的基线展示值（与本期同一换算因子）；缺省回退 baseline_value */
  display_baseline_value?: number | null
  /** 展示单位（如 Kbps）；缺省回退 unit */
  display_unit?: string | null
  /** 实际生效的除数（> 1 表示开启了换算） */
  display_factor?: number | null
}

/* ---------- 模板 ---------- */

export type BindingType = 'metric' | 'insight'

/** 「AI 摘要」槽位的写作配置（对齐后端 schemas.template.InsightAIConfig） */
export interface InsightAIConfig {
  /** 本段写作要求：想总结什么、以什么口吻（如"只总结与 Gi0/0/21 相关的"） */
  prompt?: string | null
  /** 参考范文：本段输出的**模板**，AI 照它的句式与条目数写，只把数值与单位换成真实数据（可选） */
  sample_text?: string | null
  /** 本段字数上限（留空 = 200；填了更长的范文则按范文长度为准） */
  max_chars?: number | null
  /** 采样温度（范文仿写建议 0.3~0.5，越大越发散） */
  temperature?: number | null
  /** 显著波动阈值（百分数）；留空用全局配置 */
  threshold_pct?: number | null
  /**
   * 本段专属的**基础规则**（system 规则 1-5）：留空 = 用全局配置，全局也为空 = 用内置默认。
   * 只放规则，不要把用户内容塞进来（这段会进 system）。
   */
  system_prompt?: string | null
  /**
   * 本段专属的**追加规则**（规则 6-8，仅在本段配了写作要求/范文时才生效）：
   * 留空 = 用全局配置，全局也为空 = 用内置默认。
   */
  system_rules?: string | null
}

export interface BindingRead {
  id: number
  placeholder: string
  binding_type: BindingType
  binding_ref_id: number | null
  binding_key: string | null
  /** 仅 insight 有值 */
  ai_config: InsightAIConfig | null
}

export interface BindingItem {
  placeholder: string
  binding_type: BindingType
  binding_ref_id?: number | null
  binding_key?: string | null
  /** 仅 insight 有意义 */
  ai_config?: InsightAIConfig | null
}

export type TemplatePermission = 'manage' | 'edit' | 'view'

export interface VersionRead {
  id: number
  version_no: number
  file_name: string
  placeholder_count: number
  source: string
  is_current: boolean
  created_at: string
}

export interface GrantRead {
  user_id: number
  username: string
  display_name: string
  permission: 'view' | 'edit'
}

export interface GrantItem {
  user_id: number
  permission: 'view' | 'edit'
}

export interface EditorConfig {
  enabled: boolean
  docserver_url: string
  config: Record<string, unknown>
}

export interface TemplateListItem {
  id: number
  name: string
  file_name: string
  placeholder_count: number
  bound_count: number
  is_active: boolean
  created_at: string
  owner_id: number | null
  owner_name: string | null
  current_version_no: number | null
  permission: TemplatePermission
}

export interface PlaceholderInfo {
  name: string
  /** 变量是否独占一个段落（图表类型指标绑定时必须为 true） */
  is_alone_in_paragraph: boolean
}

export interface TemplateDetail {
  id: number
  name: string
  is_active: boolean
  created_at: string
  owner_id: number | null
  owner_name: string | null
  permission: TemplatePermission
  current_version: VersionRead | null
  placeholders: PlaceholderInfo[]
  bindings: BindingRead[]
  unbound: string[]
}

/* ---------- 报告 ---------- */

export type ReportStatus =
  | 'pending'
  | 'generating'
  | 'pending_confirmation'
  | 'confirmed'
  | 'pushed'
  | 'push_failed'
  | 'failed'

export interface ReportRead {
  id: number
  /** 任务触发时为任务 id；手动实例为 null */
  task_id: number | null
  template_id: number
  version_id: number | null
  /** cron | manual */
  source: string
  period: string
  status: ReportStatus
  recipient_emails: string[]
  confirmer_email: string | null
  file_path: string | null
  last_error: string | null
  error_stage: string | null
  confirmation_sent_at: string | null
  expires_at: string | null
  confirmed_at: string | null
  last_reminder_at: string | null
  pushed_at: string | null
  /** 系统（cron）触发时为 null */
  created_by: number | null
  created_at: string
}

export interface ReportListItem extends ReportRead {
  template_name: string
  /** 来源：任务触发时给任务名（前端可跳转），手动为 null */
  task_name: string | null
}

export interface AuditRead {
  id: number
  action: string
  comment: string | null
  auditor_id: number | null
  created_at: string
}

export interface PushRecord {
  id: number
  report_id: number
  report_name: string
  attempt: number
  action: 'push' | 'retry_push'
  status: 'success' | 'failed'
  recipients: string[]
  recipient_count: number
  attachment_name: string | null
  duration_ms: number | null
  error: string | null
  actor_id: number | null
  created_at: string
}

export interface InsightSlot {
  text: string
  /** 凭空编造 / 未能溯源的数字（需人工核对） */
  unverified_numbers: string[]
  /** 照抄了参考范文里的示例数字（与 unverified_numbers 分开提示） */
  sample_leak?: string[]
  degraded: boolean
  /** 留痕：本次实际使用的提示词，便于复现"这篇为什么这么写" */
  prompt_used?: string
  /** 留痕：本次实际使用的参考范文 */
  sample_used?: string
  model?: string
  generated_at?: string
  /** 是否因命中范文护栏（抄示例数字 / 条目数超出范文）触发过自我纠正重试 */
  retried?: boolean
}

/** 试跑（模板绑定页）：只算 facts + 调 AI，不生成 docx */
export interface InsightPreviewResult {
  text: string
  unverified_numbers: string[]
  sample_leak: string[]
  degraded: boolean
  retried: boolean
  model: string
  /** 参与本次生成的指标数 */
  metric_count: number
}

/** 单段摘要重生成结果 */
export interface InsightSlotResult {
  slot: string
  text: string
  unverified_numbers: string[]
  sample_leak: string[]
  degraded: boolean
  retried: boolean
  model: string
}

export interface ReportDetail extends ReportRead {
  facts_json: MetricFact[]
  insights_json: Record<string, InsightSlot>
  audits: AuditRead[]
}

// --------------------------------------------------------------------------
// AI 调用留痕（ai_call_logs）：每一次「请求 AI」的完整参数与 prompt
// --------------------------------------------------------------------------
/** 一次 AI 请求的轮廓（列表项；messages / 响应正文点开再看） */
export interface AiCallItem {
  id: number
  /** generate 报告生成 / regenerate 单段重生成 / preview 模板试跑 */
  kind: 'generate' | 'regenerate' | 'preview'
  /** 摘要槽位（AI 占位符名） */
  slot: string | null
  period: string | null
  model: string
  status: 'success' | 'degraded' | 'failed'
  /** 是否命中范文护栏并发起过第二次请求 */
  retried: boolean
  /** 是否带自定义提示词 / 范文 */
  has_custom: boolean
  duration_ms: number | null
  created_at: string
}

export interface AiChatMessage {
  role: string
  content: string
}

/** 完整请求快照：不含 api_key，base_url 已去 userinfo */
export interface AiCallDetail extends AiCallItem {
  report_id: number | null
  template_id: number | null
  actor_id: number | null
  base_url: string
  temperature: number | null
  max_tokens: number | null
  timeout_seconds: number | null
  /** 真正发出去的 messages：system 红线 + user 段（含指标 JSON） */
  messages: AiChatMessage[]
  response_text: string | null
  error: string | null
  /** 护栏重试的第二次请求（命中照抄数字 / 整句复述时才有） */
  retry_messages: AiChatMessage[] | null
  retry_response_text: string | null
  retry_error: string | null
}

export interface AiCallList {
  total: number
  items: AiCallItem[]
}

export interface ReportCreate {
  template_id: number
  period: string
  recipient_emails: string[]
  /** 确认人邮箱（必填）：确认链接会发到这里 */
  confirmer_email: string
}

// --------------------------------------------------------------------------
// 报告任务（周期任务）
// --------------------------------------------------------------------------
export interface ReportTaskRead {
  id: number
  name: string
  template_id: number
  template_name: string | null
  cron_expr: string
  /** 统计周期粒度：month=月报（默认）/ week=周报 */
  period_granularity: 'month' | 'week'
  /** 周期偏移：0=触发时刻所在周期（默认）/ -1=上一个周期（周一触发取上周） */
  period_offset: number
  is_active: boolean
  confirmer_email: string
  /** 收件人邮箱清单（编辑时回填；只有 count 会导致保存时清空原有收件人） */
  recipient_emails: string[]
  recipient_count: number
  created_by: number
  created_at: string
  updated_at: string
  last_run_at: string | null
  next_run_at: string | null
  last_skipped_at: string | null
  last_error: string | null
  /** 该任务下"待确认"的积压数量 */
  pending_confirmation: number
}

export interface ReportTaskCreate {
  name: string
  template_id: number
  cron_expr: string
  /** 统计周期：粒度 + 偏移；不传则后端按 month/0（月报本期，等同升级前） */
  period_granularity?: 'month' | 'week'
  period_offset?: number
  confirmer_email: string
  recipient_emails: string[]
  is_active: boolean
}

export type ReportTaskUpdate = Partial<Omit<ReportTaskCreate, never>>

export interface ReportTaskRunResult {
  report_id: number | null
  status: 'created' | 'skipped'
  reason?: string | null
}

// --------------------------------------------------------------------------
// 确认（公开接口，无需登录）
// --------------------------------------------------------------------------
export type ConfirmTokenState = 'valid' | 'invalid' | 'used' | 'expired'

export interface ConfirmPreview {
  report_id: number
  report_name: string
  period: string
  status: ReportStatus
  confirmer_email: string
  recipient_emails: string[]
  created_at: string
  expires_at: string | null
  is_expired: boolean
  ai_summary: string | null
  metric_count: number
  abnormal_count: number
  token_state: ConfirmTokenState
  message: string | null
}

export interface ConfirmResult {
  report_id: number
  confirmed: boolean
  push_status: 'pushed' | 'push_failed'
  push_error?: string | null
  message: string
}
