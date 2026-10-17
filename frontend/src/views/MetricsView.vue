<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import axios from 'axios'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { MoreFilled, QuestionFilled } from '@element-plus/icons-vue'

import { confirmDelete } from '@/components/ConfirmDialog'
import AiopsScopePicker from '@/components/AiopsScopePicker.vue'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import PeriodPicker from '@/components/PeriodPicker.vue'
import TestResultAlert from '@/components/TestResultAlert.vue'
import { dataSourceApi } from '@/api/datasources'
import { metricApi } from '@/api/metrics'
import type {
  AggType,
  CompareType,
  DataSourceRead,
  HostGroupRef,
  HostRef,
  ItemRef,
  MetricDefinitionCreate,
  MetricDefinitionRead,
  MetricFact,
  MetricListItem,
  MetricType,
  PieSuitability,
  QueryConfig,
  QueryConfigResolved,
  UnitScaleConfig,
} from '@/api/types'
import { useChart } from '@/composables/useChart'
import { buildCurveOption, buildPieOption } from '@/utils/chart'
import {
  TIME_MODULES,
  TIME_QUICK_OPTIONS,
  timeFormatPreview,
  timeOffsetLabel,
} from '@/utils/time'
import type { TimeQuickOffset } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { parseApiError } from '@/utils/error'
import { type AiopsPick, buildPicksFromRefs } from '@/utils/aiopsRef'
import {
  changePctClass,
  factDisplayNumber,
  factDisplayUnit,
  factNumber,
  factShape,
  factValueText,
  foldTags,
  formatChangePct,
  formatFactValue,
  formatScope,
  METRIC_TYPE_LABEL,
  metricTypeClass,
  scopeNamesFrom,
  TYPE_ENABLED,
} from '@/utils/metric'
import { disabledUnitConfig, joinValueUnit, resolveScale } from '@/utils/unit'
import {
  baseUnitSuggestion,
  EXAMPLE_RAW_VALUE,
  isConvertibleUnit,
  METRIC_UNIT_GROUPS,
  standardBaseOf,
  type UnitBase,
} from '@/utils/metricUnits'
import { previousPeriod } from '@/utils/format'

const auth = useAuthStore()

/** 指标类型（顺序即弹窗 radio 顺序） */
const METRIC_TYPES: MetricType[] = ['scalar', 'curve', 'pie', 'text', 'time']

const rows = ref<MetricListItem[]>([])
const dataSources = ref<DataSourceRead[]>([])
const loading = ref(false)
const dsMap = computed(() => new Map(dataSources.value.map((d) => [d.id, d.name])))

/* ---- 顶部搜索 ---- */
const tagFilter = ref<string[]>([])
const nameFilter = ref('')
const statusFilter = ref<'' | 'active' | 'inactive'>('')
const allTags = ref<string[]>([])
const visibleRows = computed(() =>
  rows.value.filter((r) => {
    if (nameFilter.value && !r.name.toLowerCase().includes(nameFilter.value.trim().toLowerCase())) {
      return false
    }
    if (statusFilter.value === 'active' && !r.is_active) return false
    if (statusFilter.value === 'inactive' && r.is_active) return false
    return true
  }),
)

/* ---- 新增/编辑弹窗 ---- */
const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref<number | null>(null)
const formRef = ref<FormInstance>()

// 时间类型配置（不取数：基准=计算时刻 + 偏移 + 自定义格式）
const timeForm = reactive({
  format_modules: ['年', '-', '月'] as string[],
  zero_pad: true,
  /** 手动偏移 / 快速偏移（二选一） */
  offset_mode: 'manual' as 'manual' | 'quick',
  offset_value: 0,
  offset_unit: 'day' as 'day' | 'month',
  quick_offset: 'last_week_monday' as TimeQuickOffset | null,
})

const form = reactive({
  metric_type: 'scalar' as MetricType,
  name: '',
  data_source_id: null as number | null,
  unit: '',
  period_type: 'monthly',
  display_precision: null as number | null,
  tags: [] as string[],
  host_group_ids: [] as string[],
  host_ids: [] as string[],
  // AIOps：模型 / 实例（语义对应主机组 / 主机，由 AiopsScopePicker 维护）
  model_ids: [] as string[],
  instance_ids: [] as string[],
  item_ids: [] as string[],
  agg: 'avg' as AggType,
  compare: 'none' as CompareType,
  baseline_lookback: null as number | null,
  // scalar 数值变换：value = raw * mul + add
  transform_mul: 1,
  transform_add: 0,
  // 单位换算（仅 scalar / curve）：勾选后按进制自动选档，只改展示口径，事实层数值不变
  unit_convert: false,
  /** 换算进制：预置单位自动预填标准进制，自定义单位必须手填（null = 未填） */
  unit_base: null as UnitBase | null,
  /** 存量 unit_config 无法无损映射时的提示（fixed 除数 / 独立后缀），保存后按新语义生效 */
  unit_legacy_notice: null as string | null,
  // text
  text_mode: 'latest' as 'latest' | 'fixed',
  text_fixed_value: '',
  // curve：series 编辑器
  series: [] as { item_id: string; display_name: string; mul: number; add: number }[],
  // pie（单选监控项：single-select 不能绑数组，故用独立标量字段）
  pie_item_id: '',
  pie_up: [] as string[],
  pie_down: [] as string[],
  pie_unknown_mode: 'exclude' as 'exclude' | 'error',
  pie_ack_risk: false,
})
/** 曲线编辑器里正在搜索的 series 下拉（同一组共用远程搜索结果） */
const seriesSearch = ref('')
const pieUpInput = ref('')
const pieDownInput = ref('')
const tagInput = ref('')

/* ---- 三级级联下拉 ---- */
const groupOptions = ref<HostGroupRef[]>([])
const hostOptions = ref<HostRef[]>([])
const itemOptions = ref<ItemRef[]>([])
const loadingGroups = ref(false)
const loadingHosts = ref(false)
const loadingItems = ref(false)

// 会话内缓存：主机组按数据源，主机按 group_ids，监控项按 host_ids
const groupCache = new Map<number, HostGroupRef[]>()
const hostCache = new Map<string, HostRef[]>()
const itemCache = new Map<string, ItemRef[]>()
let groupAbort: AbortController | null = null
let hostAbort: AbortController | null = null
let itemAbort: AbortController | null = null

const dialogTitle = computed(() => (editingId.value === null ? '新增指标' : '编辑指标'))
const dsId = computed(() => form.data_source_id)

/* ---- AIOps 取数（独立级联组件，与 Zabbix 三级联并存） ---- */

/** 所选数据源的类型：决定用哪一套级联、能不能选饼图 */
const dsType = computed(
  () => dataSources.value.find((d) => d.id === dsId.value)?.type ?? '',
)
const isAiopsDs = computed(() => dsType.value === 'aiops')

/** AIOps 级联选出的取数对象（存的是定位串） */
const aiopsPicks = ref<AiopsPick[]>([])

/**
 * AIOps 当前实例下所有可选取数对象 —— 曲线的「曲线配置」下拉用它。
 * （与 Zabbix 的 itemOptions 同一位置，故下面把 itemOptions 指向它）
 */
const aiopsCandidates = ref<AiopsPick[]>([])

/** 曲线配置的下拉选项：AIOps 用候选（定位串），Zabbix 用监控项列表 */
const seriesOptionSource = computed<ItemRef[]>(() =>
  isAiopsDs.value
    ? aiopsCandidates.value.map((p) => ({
        item_id: p.ref,
        name: p.label,
        key: '',
        host_id: form.instance_ids[0] ?? '',
        host: '',
        value_type: 0,
        units: p.column?.unit ?? '',
      }))
    : itemOptions.value,
)

/** AIOps 下模型/实例由独立组件维护；切数据源时必须清干净 */
watch(dsId, () => {
  form.model_ids = []
  form.instance_ids = []
  aiopsPicks.value = []
})

/**
 * 真正落库的取数对象 id 列表。
 * AIOps 存定位串（n:/l: 前缀），Zabbix 存纯数字 item_id —— 两者不可混用。
 */
/**
 * 真正落库的取数对象 id 列表。
 * AIOps 存定位串（n:/l: 前缀），Zabbix 存纯数字 item_id —— 两者不可混用。
 */
const effectiveItemIds = computed(() =>
  isAiopsDs.value ? aiopsPicks.value.map((p) => p.ref) : [...form.item_ids],
)

/**
 * 曲线列表。两种数据源都走form.series（由「曲线配置」写入），
 * 只是 AIOps 的下拉选项来自AiopsScopePicker 抛出的候选。
 */
const effectiveSeries = computed(() => form.series)

/** AIOps 不支持饼图（无 Zabbix 那样的 UP/DOWN 值域语义），选到就落回 scalar */
watch(
  () => form.metric_type,
  (t) => {
    if (isAiopsDs.value && t === 'pie') form.metric_type = 'scalar'
  },
)

/* ---- 试算抽屉 ---- */
const drawVisible = ref(false)
const drawMetricId = ref<number | null>(null)
const drawPrecision = ref<number | null>(null)
const drawPeriod = ref(previousPeriod())
const computing = ref(false)
const fact = ref<MetricFact | null>(null)
const computeError = ref<string | null>(null)

const rules = computed<FormRules>(() => ({
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  data_source_id: [
    {
      // 时间类型不取数，不需要数据源
      validator: (_r, v, cb) =>
        form.metric_type === 'time' || v !== null && v !== undefined
          ? cb()
          : cb(new Error('请选择数据源')),
      trigger: 'change',
    },
  ],
  display_precision: [
    {
      validator: (_r, v, cb) => {
        if (v === null || v === undefined || v === '') return cb()
        const n = Number(v)
        if (!Number.isInteger(n) || n < 0 || n > 6) return cb(new Error('精度需为 0-6 的整数'))
        cb()
      },
      trigger: 'blur',
    },
  ],
  // 单位 + 换算：勾了换算就必须有可用的基准单位与进制，避免把 422 丢给后端
  unit: [
    {
      validator: (_r, _v, cb) => {
        if (!unitRowVisible.value || !form.unit_convert) return cb()
        if (!isConvertibleUnit(form.unit)) {
          return cb(new Error('该单位已带 K/M/G/T 前缀，请改选基准单位（如 bps）'))
        }
        if (form.unit_base === null) return cb(new Error('请选择换算进制（1000 或 1024）'))
        cb()
      },
      trigger: 'change',
    },
  ],
  host_group_ids: [
    {
      type: 'array',
      required: true,
      validator: (_r, _v, cb) =>
        form.host_group_ids.length > 0 ? cb() : cb(new Error('至少选择一个主机组')),
      trigger: 'change',
    },
  ],
  host_ids: [
    {
      type: 'array',
      required: true,
      validator: (_r, _v, cb) =>
        form.host_ids.length > 0 ? cb() : cb(new Error('至少选择一台主机')),
      trigger: 'change',
    },
  ],
  item_ids: [
    {
      type: 'array',
      required: false,
      validator: (_r, _v, cb) => {
        if (form.metric_type === 'time') return cb() // 时间类型不取数
        if (form.metric_type === 'curve') return cb() // curve 用 series
        if (form.metric_type === 'pie') {
          // pie 用标量 pie_item_id，item_ids 规则不适用
          return form.pie_item_id ? cb() : cb(new Error('pie 需选择一个监控项'))
        }
        if (form.metric_type === 'text' && form.text_mode === 'fixed') return cb()
        if (form.metric_type === 'text') {
          return form.item_ids.length === 1
            ? cb()
            : cb(new Error('最新值模式需选择恰好一个监控项'))
        }
        return form.item_ids.length > 0 ? cb() : cb(new Error('至少选择一个监控项'))
      },
      trigger: 'change',
    },
  ],
  time_quick_offset: [
    {
      validator: (_r, _v, cb) =>
        timeForm.offset_mode !== 'quick' || timeForm.quick_offset
          ? cb()
          : cb(new Error('请选择快速偏移选项')),
      trigger: 'change',
    },
  ],
  baseline_lookback: [
    {
      validator: (_r, v, cb) => {
        if (form.compare === 'none') return cb()
        const n = Number(v)
        if (!Number.isInteger(n) || n < 1) return cb(new Error('compare 非 none 时回溯周期需为 >=1 的整数'))
        cb()
      },
      trigger: 'blur',
    },
  ],
}))

async function load() {
  loading.value = true
  try {
    const [m, ds] = await Promise.all([metricApi.list(tagFilter.value), dataSourceApi.list()])
    rows.value = m.data
    dataSources.value = ds.data.filter((d) => d.is_active)
    const tags = new Set<string>()
    rows.value.forEach((r) => r.tags?.forEach((t) => tags.add(t)))
    allTags.value = [...tags]
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    loading.value = false
  }
}

function resetCaches() {
  groupCache.clear()
  hostCache.clear()
  itemCache.clear()
}

/** 回填时间类型配置（缺省给 年-月，兼容旧数据/未配置） */
function fillTimeConfig(tc: Record<string, unknown> | undefined) {
  const c = (tc?.time_config ?? {}) as Partial<{
    format_modules: string[]
    zero_pad: boolean
    offset_mode: 'manual' | 'quick'
    offset_value: number
    offset_unit: 'day' | 'month'
    quick_offset: TimeQuickOffset | null
  }>
  timeForm.format_modules =
    Array.isArray(c.format_modules) && c.format_modules.length
      ? [...c.format_modules]
      : ['年', '-', '月']
  timeForm.zero_pad = c.zero_pad !== false
  // 旧数据没有 offset_mode，一律按手动偏移处理
  timeForm.offset_mode = c.offset_mode === 'quick' ? 'quick' : 'manual'
  timeForm.offset_value = typeof c.offset_value === 'number' ? c.offset_value : 0
  timeForm.offset_unit = c.offset_unit === 'month' ? 'month' : 'day'
  timeForm.quick_offset = c.quick_offset ?? 'last_week_monday'
}

function resetForm() {
  fillTimeConfig(undefined)
  Object.assign(form, {
    metric_type: 'scalar',
    name: '',
    data_source_id: null,
    unit: '',
    period_type: 'monthly',
    display_precision: null,
    tags: [],
    host_group_ids: [],
    host_ids: [],
    item_ids: [],
    agg: 'avg' as AggType,
    compare: 'none' as CompareType,
    baseline_lookback: null,
    transform_mul: 1,
    transform_add: 0,
    unit_convert: false,
    unit_base: null,
    unit_legacy_notice: null,
    text_mode: 'latest',
    text_fixed_value: '',
    series: [],
    pie_item_id: '',
    pie_up: [],
    pie_down: [],
    pie_unknown_mode: 'exclude',
    pie_ack_risk: false,
  })
  seriesSearch.value = ''
  tagInput.value = ''
  groupOptions.value = []
  hostOptions.value = []
  itemOptions.value = []
}

function openCreate() {
  editingId.value = null
  resetForm()
  resetCaches()
  dialogVisible.value = true
  formRef.value?.clearValidate()
}

/** 时间类型的"取数范围"摘要：如 "年-月 · 偏移 -1月"（时间类型不取数） */
function timeScopeSummary(row: MetricListItem): string {
  const c = row.type_config?.time_config
  const fmt = (c?.format_modules ?? []).join('') || '年-月'
  const off = timeOffsetLabel(Number(c?.offset_value ?? 0), c?.offset_unit ?? 'day')
  return `${fmt} · ${off}`
}

async function openEdit(row: MetricDefinitionRead) {
  editingId.value = row.id
  resetCaches()
  resetForm()
  Object.assign(form, {
    metric_type: row.metric_type ?? 'scalar',
    name: row.name,
    data_source_id: row.data_source_id,
    unit: row.unit,
    period_type: row.period_type,
    display_precision: row.display_precision,
    tags: [...(row.tags ?? [])],
    agg: row.calc_formula.agg,
    compare: row.calc_formula.compare,
    baseline_lookback: row.calc_formula.baseline_lookback ?? null,
    transform_mul: row.type_config?.transform?.mul ?? 1,
    transform_add: row.type_config?.transform?.add ?? 0,
    ...unitFormFromConfig(row.unit_config, row.unit),
    text_mode: row.type_config?.text_config?.mode ?? 'latest',
    text_fixed_value: row.type_config?.text_config?.fixed_value ?? '',
    series: (row.query_config.series ?? []).map((x) => ({
      item_id: String(x.item_id ?? ''),
      display_name: x.display_name ?? '',
      mul: Number(x.transform?.mul ?? 1),
      add: Number(x.transform?.add ?? 0),
    })),
    pie_item_id: String(row.query_config.item_ids?.[0] ?? ''),
    pie_up: [...(row.type_config?.pie_config?.up_values ?? ['1'])],
    pie_down: [...(row.type_config?.pie_config?.down_values ?? ['0'])],
    pie_unknown_mode: row.type_config?.pie_config?.unknown_mode ?? 'exclude',
    pie_ack_risk: false,
  })
  dialogVisible.value = true
  formRef.value?.clearValidate()

  // 详情带回 id + 名称（避免逐级反查多次）
  let resolved: QueryConfigResolved | null = null
  try {
    const { data } = await metricApi.detail(row.id)
    resolved = data.query_config_resolved
  } catch {
    /* 名称解析失败时降级为仅 id */
  }
  form.host_group_ids = [...(row.query_config.host_group_ids ?? [])]
  form.host_ids = [...(row.query_config.host_ids ?? [])]
  form.item_ids = [...(row.query_config.item_ids ?? [])]

  // AIOps：范围是「模型 / 实例」，取数对象是**定位串**（n:/l: 前缀）。
  // 必须一起回填，否则 AiopsScopePicker 挂载时会读到空的 scope/picks，
  // 而它的 immediate watcher 又会把这些值清掉 —— 编辑一个AIOps 指标会丢配置。
  form.model_ids = [...(row.query_config.model_ids ?? [])]
  form.instance_ids = [...(row.query_config.instance_ids ?? [])]
  // **两条路径都要读**：scalar/pie/text 的取数对象在 item_ids，
  // curve 的在 series[].item_id（它的 item_ids 是空的）。只读一条的话，
  // 编辑曲线指标时 picks 为空 -> 回显 watcher 直接 return -> 实例列表不加载 ->
  // el-select 只能把原始 id 当标签显示。
  const aiopsRefs = [
    ...(row.query_config.item_ids ?? []),
    ...(row.query_config.series ?? [])
      .map((s) => (s as { item_id?: string }).item_id)
      .filter((x): x is string => typeof x === 'string' && x !== ''),
  ]
  aiopsPicks.value = buildPicksFromRefs(aiopsRefs)
  // 曲线的取数对象与展示名/数值变换都存在 series 里，一并回填
  form.series = (row.query_config.series ?? []).map((s) => {
    const one = s as { item_id?: string; display_name?: string; transform?: { mul?: number; add?: number } }
    return {
      item_id: one.item_id ?? '',
      display_name: one.display_name ?? '',
      mul: one.transform?.mul ?? 1,
      add: one.transform?.add ?? 0,
    }
  })

  // 时间类型不取数：回填时间配置即可，不需要拉取主机组/主机/监控项
  if (form.metric_type === 'time') {
    fillTimeConfig(row.type_config as Record<string, unknown> | undefined)
    return
  }

  const id = row.data_source_id
  if (isAiopsDs.value) {
    // AIOps 的模型 / 实例 / 监控项由 AiopsScopePicker 自己按需加载，
    // 这里不要抢跑（抢跑会把它的内部状态与本表单的 scope 搅在一起）
    mergeResolved(resolved)
    return
  }
  await loadGroups(id)
  if (form.host_group_ids.length) await loadHosts(id, form.host_group_ids)
  if (form.host_ids.length) await loadItems(id, form.host_ids)
  mergeResolved(resolved)
}

/** 把详情解析出的名称补进下拉选项（缓存里没有的 id 也要能显示名称） */
function mergeResolved(resolved: QueryConfigResolved | null) {
  if (!resolved) return
  resolved.host_groups.forEach((g) => {
    if (!groupOptions.value.some((o) => o.group_id === g.id)) {
      groupOptions.value.push({ group_id: g.id, name: g.name })
    }
  })
  resolved.hosts.forEach((h) => {
    if (!hostOptions.value.some((o) => o.host_id === h.id)) {
      hostOptions.value.push({ host_id: h.id, name: h.name, groups: [] })
    }
  })
  resolved.items.forEach((i) => {
    if (!itemOptions.value.some((o) => o.item_id === i.id)) {
      itemOptions.value.push({
        item_id: i.id,
        name: i.name,
        key: '',
        host_id: i.host_id,
        host: i.host,
        value_type: 0,
        units: '',
      })
    }
  })
}

async function loadGroups(datasourceId: number) {
  groupAbort?.abort()
  groupAbort = new AbortController()
  loadingGroups.value = true
  try {
    const cached = groupCache.get(datasourceId)
    if (cached) {
      groupOptions.value = [...cached]
      return
    }
    const { data } = await dataSourceApi.listHostGroups(datasourceId)
    groupOptions.value = data
    groupCache.set(datasourceId, data)
  } catch (error) {
    if (!axios.isCancel(error)) ElMessage.error(parseApiError(error).message)
  } finally {
    loadingGroups.value = false
  }
}

async function loadHosts(datasourceId: number, groupIds: string[]) {
  if (!groupIds.length) {
    hostOptions.value = []
    return
  }
  const key = `${datasourceId}|${[...groupIds].sort().join(',')}`
  hostAbort?.abort()
  hostAbort = new AbortController()
  loadingHosts.value = true
  try {
    const cached = hostCache.get(key)
    if (cached) {
      hostOptions.value = [...cached]
      return
    }
    const { data } = await dataSourceApi.listHosts(datasourceId, groupIds)
    hostOptions.value = data
    hostCache.set(key, data)
  } catch (error) {
    if (!axios.isCancel(error)) ElMessage.error(parseApiError(error).message)
  } finally {
    loadingHosts.value = false
  }
}

async function loadItems(datasourceId: number, hostIds: string[], q = '') {
  if (!hostIds.length) {
    itemOptions.value = []
    return
  }
  const key = `${datasourceId}|${[...hostIds].sort().join(',')}`
  if (!q) {
    const cached = itemCache.get(key)
    if (cached) {
      itemOptions.value = [...cached]
      return
    }
  }
  itemAbort?.abort()
  itemAbort = new AbortController()
  loadingItems.value = true
  try {
    const { data } = await dataSourceApi.listItems(datasourceId, hostIds, q || undefined)
    itemOptions.value = data
    if (!q) itemCache.set(key, data)
  } catch (error) {
    if (!axios.isCancel(error)) ElMessage.error(parseApiError(error).message)
  } finally {
    loadingItems.value = false
  }
}

/* ---- 级联：变更上级清空下级 ---- */
function onDataSourceChange() {
  form.host_group_ids = []
  form.host_ids = []
  form.item_ids = []
  groupOptions.value = []
  hostOptions.value = []
  itemOptions.value = []
  if (dsId.value !== null) void loadGroups(dsId.value)
}

function onGroupsChange() {
  form.host_ids = []
  form.item_ids = []
  hostOptions.value = []
  itemOptions.value = []
  if (dsId.value !== null) void loadHosts(dsId.value, form.host_group_ids)
}

function onHostsChange() {
  form.item_ids = []
  itemOptions.value = []
  form.pie_item_id = ''
  pieSuitability.value = null
  form.pie_ack_risk = false
  if (dsId.value !== null) void loadItems(dsId.value, form.host_ids)
}

function onItemSearch(q: string) {
  if (dsId.value !== null) void loadItems(dsId.value, form.host_ids, q)
}

/** 切换指标类型：清空不再适用的监控项选择，避免脏数据提交 */
function onMetricTypeChange() {
  pieSuitability.value = null
  form.pie_ack_risk = false
  form.pie_item_id = ''
  if (form.metric_type === 'curve') {
    form.item_ids = []
  } else if (form.metric_type === 'time') {
    // 时间类型不取数：清空三级取数范围与数据源
    form.data_source_id = null
    form.host_group_ids = []
    form.host_ids = []
    form.item_ids = []
    form.series = []
  } else if (form.metric_type === 'text' && form.text_mode === 'fixed') {
    form.item_ids = []
  } else if (form.item_ids.length > 1) {
    form.item_ids = form.item_ids.slice(0, 1)
  }
  formRef.value?.validateField('item_ids').catch(() => undefined)
}

/* ---- 时间类型：格式模块增删与顺序 ---- */
function addTimeModule(m: string) {
  if (timeForm.format_modules.length >= 20) {
    ElMessage.warning('格式模块最多 20 个')
    return
  }
  timeForm.format_modules.push(m)
}

function removeTimeModule(idx: number) {
  timeForm.format_modules.splice(idx, 1)
}

/** 切换偏移方式：快速偏移给一个默认选项，避免停留在"未选"状态 */
function onTimeOffsetModeChange(mode: 'manual' | 'quick') {
  if (mode === 'quick' && !timeForm.quick_offset) {
    timeForm.quick_offset = 'last_week_monday'
  }
}

/** 实时示例：只显示最终格式结果 */
const timePreview = computed(() =>
  timeFormatPreview(timeForm.format_modules, timeForm.zero_pad, {
    mode: timeForm.offset_mode,
    value: Number(timeForm.offset_value) || 0,
    unit: timeForm.offset_unit,
    quick: timeForm.quick_offset,
  }),
)

/* ---- curve: series 增删改 ---- */
function addSeriesRow() {
  form.series.push({ item_id: '', display_name: '', mul: 1, add: 0 })
}

function removeSeriesRow(idx: number) {
  form.series.splice(idx, 1)
}

function onSeriesItemChange(row: { item_id: string; display_name: string }) {
  // 选中 item 时用 item 名称预填 display_name（用户可再改）
  const it = itemOptions.value.find((x) => x.item_id === row.item_id)
  if (it && !row.display_name) {
    row.display_name = it.name
  }
}

function onSeriesSearch(q: string) {
  seriesSearch.value = q
  if (dsId.value !== null) void loadItems(dsId.value, form.host_ids, q)
}

function addPieValue(side: 'up' | 'down') {
  const raw = (side === 'up' ? pieUpInput.value : pieDownInput.value).trim()
  if (side === 'up') pieUpInput.value = ''
  else pieDownInput.value = ''
  if (!raw) return
  const target = side === 'up' ? form.pie_up : form.pie_down
  if (!target.includes(raw)) target.push(raw)
}

/* ---- pie: 适用性判定 ---- */
const pieSuitability = ref<PieSuitability | null>(null)
const pieChecking = ref(false)

async function checkPieSuitability() {
  const itemId = form.pie_item_id
  if (dsId.value === null || !itemId) {
    ElMessage.warning('请先选择主机组、主机与监控项')
    return
  }
  pieChecking.value = true
  pieSuitability.value = null
  try {
    const { data } = await dataSourceApi.analyzePieSuitability(dsId.value, {
      host_group_ids: [...form.host_group_ids],
      host_ids: [...form.host_ids],
      item_id: itemId,
    })
    pieSuitability.value = data
    form.pie_ack_risk = false
    if (data.suggested_config) {
      form.pie_up = [...data.suggested_config.up_values]
      form.pie_down = [...data.suggested_config.down_values]
    }
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    pieChecking.value = false
  }
}

/** pie 切换 item 后清空判定结果，避免用旧结论 */
function onPieItemChange() {
  pieSuitability.value = null
  form.pie_ack_risk = false
}

/* ---- 试算抽屉：展示口径（单位换算后优先） ---- */
/** 基线展示值：换算后优先（与本期同一因子），缺省回退原始基线 */
const factDisplayBaseline = computed<number | null>(() => {
  const f = fact.value
  if (!f || factShape(f) !== 'scalar') return null
  if (typeof f.display_baseline_value === 'number') return f.display_baseline_value
  return typeof f.baseline_value === 'number' ? f.baseline_value : null
})
/** 开启换算时补一行原始口径便于核对（262468.9041666667 这类长串就在这里） */
const scalarRawHint = computed(() => {
  const f = fact.value
  if (!f || factShape(f) !== 'scalar') return ''
  const factor = f.display_factor
  if (typeof factor !== 'number' || factor === 1) return ''
  const raw = factNumber(f)
  if (raw === null) return ''
  return joinValueUnit(String(raw), f.unit)
})

/* ---- 试算抽屉：图表 ---- */
const curveChartEl = ref<HTMLElement>()
const pieChartEl = ref<HTMLElement>()
const curveOption = computed(() => {
  if (fact.value === null || factShape(fact.value) !== 'series') return null
  const payload = fact.value.value as { series?: { name: string; points: [number, number][] }[] }
  // 单位换算：与后端 chart_renderer 同口径——先缩放点值再画，Y 轴用展示单位
  const factor =
    typeof fact.value.display_factor === 'number' && fact.value.display_factor > 0
      ? fact.value.display_factor
      : 1
  const series = (payload.series ?? []).map((s) => ({
    name: s.name,
    points: (s.points ?? []).map((p) => [p[0], p[1] / factor] as [number, number]),
  }))
  return buildCurveOption({ series, unit: factDisplayUnit(fact.value) })
})
/** pie 的 up+down 都为 0 时无可画内容 */
const pieEmpty = computed(() => {
  if (fact.value === null || factShape(fact.value) !== 'pie') return false
  const payload = fact.value.value as { up_count?: number; down_count?: number }
  return (payload.up_count ?? 0) + (payload.down_count ?? 0) === 0
})

/** 所有曲线都没有数据点时给出提示（否则 ECharts 只画空坐标系） */
const curveEmpty = computed(() => {
  if (fact.value === null || factShape(fact.value) !== 'series') return false
  const payload = fact.value.value as { series?: { points?: unknown[] }[] }
  const series = payload.series ?? []
  return series.length > 0 && series.every((x) => (x.points?.length ?? 0) === 0)
})

const pieOption = computed(() => {
  if (fact.value === null || factShape(fact.value) !== 'pie') return null
  const v = fact.value.value as {
    up_pct: number
    down_pct: number
    up_count?: number
    down_count?: number
    up_label?: string
    down_label?: string
  }
  // 标签与"0 值不画标签"的口径均来自后端 payload，与报告里的 PNG 保持一致
  return buildPieOption({
    upPct: v.up_pct,
    downPct: v.down_pct,
    upCount: v.up_count,
    downCount: v.down_count,
    upLabel: v.up_label,
    downLabel: v.down_label,
  })
})
useChart(curveChartEl, curveOption)
useChart(pieChartEl, pieOption)

function onTextModeChange() {
  if (form.text_mode === 'fixed') {
    form.item_ids = []
  } else {
    form.item_ids = []
  }
  formRef.value?.validateField('item_ids').catch(() => undefined)
}

/* ---- 标签输入 ---- */
function addTag() {
  const raw = tagInput.value.trim()
  tagInput.value = ''
  if (!raw) return
  for (const p of raw.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean)) {
    if (!form.tags.includes(p)) form.tags.push(p)
  }
}

function removeTag(idx: number) {
  form.tags.splice(idx, 1)
}

function itemLabel(item: ItemRef): string {
  return form.host_ids.length > 1 && item.host ? `${item.host}: ${item.name}` : item.name
}

/** 表格行是 any，这里收敛成 string[] 供 foldTags 使用 */
function tagsOf(row: { tags?: string[] | null }): string[] {
  return row.tags ?? []
}

/* ---- 单位 + 换算（仅 scalar / curve；只改展示口径，事实层数值不动） ---- */
/**
 * 单位行只服务连续量：pie 恒为占比（%）、text 无单位、time 不取数，这三类没有单位能力，
 * 整行不渲染（后端同样不接受它们的换算配置）。清单与规则见 utils/metricUnits.ts。
 */
const unitRowVisible = computed(
  () => form.metric_type === 'scalar' || form.metric_type === 'curve',
)

/** 当前单位能否勾选换算：带 K/M/G/T 前缀的单位会被自动选档再加一层前缀（KKbps） */
const unitConvertible = computed(() => isConvertibleUnit(form.unit))

/** 带前缀单位时可改选的基准单位（提示语给出具体建议，不做静默改写） */
const unitBaseSuggestion = computed(() => baseUnitSuggestion(form.unit))

/** 勾了换算但进制还没填（自定义单位的必填项） */
const unitBaseMissing = computed(() => form.unit_convert && form.unit_base === null)

/** 存量 unit_config -> 表单状态：none/auto 直映；fixed 与独立后缀降级并留提示 */
function unitFormFromConfig(
  config: UnitScaleConfig | null | undefined,
  currentUnit: string,
): { unit_convert: boolean; unit_base: UnitBase | null; unit_legacy_notice: string | null } {
  const mode = config?.mode ?? 'none'
  if (mode === 'none') return { unit_convert: false, unit_base: null, unit_legacy_notice: null }
  if (mode === 'fixed') {
    const divisor = Number(config?.divisor ?? 0)
    const base: UnitBase | null = divisor === 1000 ? 1000 : divisor === 1024 ? 1024 : null
    return {
      unit_convert: true,
      unit_base: base,
      unit_legacy_notice: `原配置为固定除数「÷${divisor}」，新版按进制自动选档：请确认进制并保存后生效`,
    }
  }
  const suffix = (config?.suffix_unit ?? '').trim()
  const unit = currentUnit.trim()
  return {
    unit_convert: true,
    unit_base: config?.base === 1024 ? 1024 : 1000,
    unit_legacy_notice:
      suffix && suffix !== unit
        ? `原配置的展示单位后缀为「${suffix}」，新版统一用单位「${unit || '空'}」：保存后生效`
        : null,
  }
}

/** 表单 -> unit_config：勾了换算才提交 auto，否则 {"mode":"none"}（后端存 NULL = 不换算） */
function buildUnitConfig(): UnitScaleConfig {
  if (!form.unit_convert || !unitConvertible.value || form.unit_base === null) {
    return { mode: 'none' }
  }
  // suffix_unit 刻意留空：后端 resolve() 会回退指标 unit，单位改名后不会留下陈旧后缀
  return { mode: 'auto', base: form.unit_base, suffix_unit: '' }
}

/** 选单位：预置项自动预填标准进制；自定义单位清空进制，交给校验要求手填 */
function onUnitChange(value: string | number | null | undefined) {
  const unit = String(value ?? '').trim()
  form.unit = unit
  form.unit_legacy_notice = null
  if (!unit || !isConvertibleUnit(unit)) {
    // 带前缀单位（Kbps/MB）不能再换算，直接关掉，杜绝 KKbps
    form.unit_convert = false
    form.unit_base = null
  } else {
    form.unit_base = standardBaseOf(unit)
  }
  revalidateUnit()
}

/** 勾选换算：进制还空着时用预置项的标准进制兜底 */
function onUnitConvertChange(checked: boolean | string | number) {
  if (Boolean(checked) && form.unit_base === null) form.unit_base = standardBaseOf(form.unit)
  revalidateUnit()
}

/** 进制改了立刻重新校验：要么消掉红字，要么给出提示，不让它挂着不走 */
function onUnitBaseChange() {
  revalidateUnit()
}

/** 单位行校验：把 unit / 进制 的错误摘要集中在「单位」这一项上 */
function revalidateUnit() {
  formRef.value?.validateField('unit').catch(() => undefined)
}

/** 换算预览的示例值：优先用最近一次试算的本期值，否则用固定示例（5 位整数 + 3 位小数） */
const unitPreviewRaw = computed(() => {
  const f = fact.value
  if (f && factShape(f) === 'scalar') {
    const raw = factNumber(f)
    if (raw !== null) return raw
  }
  return EXAMPLE_RAW_VALUE
})

/** 预览行是否用了示例值（而非真实试算结果） */
const unitPreviewIsSample = computed(() => {
  const f = fact.value
  return !f || factShape(f) !== 'scalar' || factNumber(f) === null
})

/** 预览文案：「原值+原单位 → 换算值+换算单位」，随输入即时更新 */
const unitPreviewText = computed(() => {
  const raw = unitPreviewRaw.value
  const before = joinValueUnit(String(raw), form.unit)
  const scale = resolveScale(unitRowVisible.value ? buildUnitConfig() : null, {
    magnitude: raw,
    baseUnit: form.unit,
    precision: form.display_precision,
  })
  if (!scale) return before
  const shown = formatFactValue(raw / scale.factor, scale.precision)
  return `${before} → ${joinValueUnit(shown, scale.unit)}`
})

/**
 * 落库单位：pie 恒为占比（%）、text 无单位、time 不取数（顺带清掉切类型后残留的旧单位）；
 * 其余类型用用户所选（含自定义）单位。
 */
/** AIOps 级联：范围变了就清掉已选取数对象（跨实例的定位串已失效） */
function onAiopsScopeChange() {
  aiopsPicks.value = []
  form.item_ids = []
  form.series = []
  pieSuitability.value = null
}

function normalizedUnit(): string {
  if (form.metric_type === 'pie') return '%'
  if (form.metric_type === 'text' || form.metric_type === 'time') return ''
  return form.unit.trim()
}

function buildPayload(): MetricDefinitionCreate {
  // 取数范围按数据源类型切换：AIOps 用「模型 / 实例」，Zabbix 用「主机组 / 主机」。
  // 两组都带上（另一组为空数组）——后端 _ScopeBase 要求「成对且至少一组完整」，
  // 空数组等价于未填。
  const scope = {
    host_group_ids: [...form.host_group_ids],
    host_ids: [...form.host_ids],
    model_ids: [...form.model_ids],
    instance_ids: [...form.instance_ids],
  }
  const common = {
    metric_type: form.metric_type,
    name: form.name,
    data_source_id: form.data_source_id,
    unit: normalizedUnit(),
    period_type: form.period_type,
    display_precision: form.display_precision,
    tags: [...form.tags],
    // pie/text/time 没有单位能力，一律不带换算，避免后端 422
    unit_config: unitRowVisible.value ? buildUnitConfig() : disabledUnitConfig(),
  }
  if (form.metric_type === 'curve') {
    return {
      ...common,
      metric_type: 'curve',
      query_config: {
        ...scope,
        item_ids: [],
        series: effectiveSeries.value
          .filter((x) => x.item_id)
          .map((x) => ({
            item_id: x.item_id,
            display_name:
              x.display_name ||
              itemOptions.value.find((i) => i.item_id === x.item_id)?.name ||
              '',
            transform: { mul: Number(x.mul), add: Number(x.add) },
          })),
      },
      type_config: {},
    }
  }
  if (form.metric_type === 'pie') {
    return {
      ...common,
      metric_type: 'pie',
      query_config: { ...scope, item_ids: effectiveItemIds.value.slice(0, 1) },
      type_config: {
        pie_config: {
          up_values: [...form.pie_up],
          down_values: [...form.pie_down],
          unknown_mode: form.pie_unknown_mode,
        },
      },
    }
  }
  if (form.metric_type === 'time') {
    // 时间类型：不带数据源、不带取数范围（time 的 query_config 为空对象）
    return {
      name: form.name,
      data_source_id: null,
      unit: normalizedUnit(),
      period_type: 'monthly',
      display_precision: form.display_precision,
      tags: [...form.tags],
      metric_type: 'time' as const,
      query_config: {} as QueryConfig,
      type_config: {
        time_config: {
          format_modules: [...timeForm.format_modules],
          zero_pad: timeForm.zero_pad,
          offset_mode: timeForm.offset_mode,
          offset_value: Number(timeForm.offset_value) || 0,
          offset_unit: timeForm.offset_unit,
          quick_offset: timeForm.offset_mode === 'quick' ? timeForm.quick_offset : null,
        },
      },
    }
  }
  if (form.metric_type === 'text') {
    return {
      ...common,
      metric_type: 'text',
      query_config: {
        ...scope,
        item_ids: form.text_mode === 'latest' ? effectiveItemIds.value.slice(0, 1) : [],
      },
      type_config: {
        text_config: {
          mode: form.text_mode,
          fixed_value: form.text_mode === 'fixed' ? form.text_fixed_value : null,
        },
      },
    }
  }
  return {
    ...common,
    metric_type: 'scalar',
    query_config: { ...scope, item_ids: effectiveItemIds.value },
    type_config: {
      transform: { mul: Number(form.transform_mul), add: Number(form.transform_add) },
    },
    calc_formula: {
      agg: form.agg,
      compare: form.compare,
      baseline_lookback: form.compare === 'none' ? null : form.baseline_lookback,
    },
  }
}

async function onSubmit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  if (form.metric_type === 'curve') {
    const filled = form.series.filter((x) => x.item_id)
    if (filled.length === 0) {
      ElMessage.warning('至少为一条曲线选择监控项')
      return
    }
    const ids = filled.map((x) => x.item_id)
    if (new Set(ids).size !== ids.length) {
      ElMessage.warning('同一监控项不能重复配置多条曲线')
      return
    }
  }
  if (form.metric_type === 'pie') {
    if (!form.pie_item_id) {
      ElMessage.warning('请选择监控项')
      return
    }
    if (form.pie_up.length === 0 || form.pie_down.length === 0) {
      ElMessage.warning('请填写 UP / DOWN 的取值')
      return
    }
    if (pieSuitability.value && pieSuitability.value.suitable === false && !form.pie_ack_risk) {
      ElMessage.warning('该监控项不适合做占比分析，请先阅读原因并勾选「我了解风险」')
      return
    }
  }
  submitting.value = true
  try {
    if (editingId.value === null) {
      await metricApi.create(buildPayload())
      ElMessage.success('指标已创建')
    } else {
      await metricApi.update(editingId.value, buildPayload())
      ElMessage.success('指标已更新')
    }
    dialogVisible.value = false
    await load()
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 409) {
      ElMessage.error('指标名称已存在')
    } else {
      ElMessage.error(parseApiError(error).message)
    }
  } finally {
    submitting.value = false
  }
}

async function onDelete(row: MetricDefinitionRead) {
  const ok = await confirmDelete(row.name)
  if (!ok) return
  try {
    await metricApi.remove(row.id)
    ElMessage.success('指标已删除')
    await load()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}

function onRowCommand(cmd: string, row: MetricDefinitionRead) {
  if (cmd === 'delete') void onDelete(row)
}

/* ---- 试算 ---- */
function openCompute(metricId: number | null, precision: number | null = null) {
  if (metricId === null) {
    ElMessage.warning('请先保存指标后再试算')
    return
  }
  drawMetricId.value = metricId
  drawPrecision.value = precision
  drawPeriod.value = previousPeriod()
  fact.value = null
  computeError.value = null
  drawVisible.value = true
  // 打开即试算：抽屉/弹窗只是结果容器，点「试算」的预期是马上看到结果
  void doCompute()
}

async function doCompute() {
  if (drawMetricId.value === null || !drawPeriod.value) {
    ElMessage.warning('请先选择统计周期')
    return
  }
  computing.value = true
  fact.value = null
  computeError.value = null
  try {
    const { data } = await metricApi.compute(drawMetricId.value, drawPeriod.value)
    fact.value = data
  } catch (error) {
    computeError.value = parseApiError(error).message
  } finally {
    computing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-container">
    <PageHeader title="指标管理" description="定义取数范围与聚合口径，产出计算变量供模板引用">
      <template #actions>
        <el-button v-if="auth.isAdmin" type="primary" @click="openCreate">新增指标</el-button>
      </template>
    </PageHeader>

    <!-- 顶部搜索区 -->
    <div class="filter-bar">
      <el-select
        v-model="tagFilter"
        multiple
        filterable
        allow-create
        default-first-option
        clearable
        collapse-tags
        collapse-tags-tooltip
        placeholder="按标签筛选（可输入新建）"
        class="filter-tags"
        data-testid="tag-filter"
        @change="load"
      >
        <el-option v-for="t in allTags" :key="t" :label="t" :value="t" />
      </el-select>
      <el-input
        v-model="nameFilter"
        placeholder="按名称搜索"
        clearable
        class="filter-name"
        data-testid="name-filter"
      />
      <el-select v-model="statusFilter" placeholder="状态" clearable class="filter-status">
        <el-option label="启用" value="active" />
        <el-option label="停用" value="inactive" />
      </el-select>
    </div>

    <DataTable :data="visibleRows" :loading="loading" empty-text="暂无指标，点击右上角新增">
      <template #columns>
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column label="类型" width="100">
          <template #default="{ row }">
            <span
              :class="[
                'soft-tag',
                `soft-tag--${metricTypeClass(row.metric_type as MetricType)}`,
              ]"
            >
              {{ METRIC_TYPE_LABEL[row.metric_type as MetricType] }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="标签" width="180">
          <template #default="{ row }">
            <div class="tag-cell">
              <span
                v-for="t in foldTags(tagsOf(row)).shown"
                :key="t"
                class="soft-tag soft-tag--info"
              >
                {{ t }}
              </span>
              <el-tooltip
                v-if="foldTags(tagsOf(row)).rest > 0"
                :content="tagsOf(row).join('、')"
                placement="top"
              >
                <span class="soft-tag soft-tag--neutral">+{{ foldTags(tagsOf(row)).rest }}</span>
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="数据源" width="150">
          <template #default="{ row }">
            <!-- time 类型不取数，没有数据源 -->
            <span v-if="row.data_source_id === null || row.data_source_id === undefined">—</span>
            <template v-else>{{ dsMap.get(row.data_source_id) ?? `#${row.data_source_id}` }}</template>
          </template>
        </el-table-column>
        <el-table-column label="取数范围" min-width="280" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="code-chip">{{
              formatScope(
                row.query_config,
                scopeNamesFrom(row.query_config_resolved),
                row.metric_type,
                row.metric_type === 'time' ? timeScopeSummary(row) : undefined,
              )
            }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <span :class="['soft-tag', row.is_active ? 'soft-tag--success' : 'soft-tag--neutral']">
              {{ row.is_active ? '启用' : '停用' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column v-if="auth.isAdmin" label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <div class="action-cell">
              <el-button link type="primary" data-testid="metric-action-edit" @click="openEdit(row)">编辑</el-button>
              <el-button link type="primary" data-testid="metric-action-compute" @click="openCompute(row.id, row.display_precision)">试算</el-button>
              <el-dropdown trigger="click" @command="(cmd: unknown) => onRowCommand(String(cmd), row)">
                <el-button link type="primary" data-testid="metric-action-more">
                  <el-icon><MoreFilled /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="delete" divided class="danger-item">删除</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </el-table-column>
      </template>
    </DataTable>

    <!-- 新增/编辑弹窗 -->
    <FormDialog v-model:visible="dialogVisible" :title="dialogTitle" :submitting="submitting" width="760px" @submit="onSubmit">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <div class="form-section">指标类型</div>
        <el-form-item label="类型" prop="metric_type">
          <el-radio-group
            v-model="form.metric_type"
            data-testid="metric-type-group"
            @change="onMetricTypeChange"
          >
            <el-radio-button
              v-for="t in METRIC_TYPES"
              :key="t"
              :label="t"
              :disabled="!TYPE_ENABLED.includes(t)"
            >
              {{ METRIC_TYPE_LABEL[t] }}
            </el-radio-button>
          </el-radio-group>
        </el-form-item>
        <div class="form-section">基础信息</div>
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="如 cpu_usage_avg" />
        </el-form-item>
        <el-form-item v-if="form.metric_type !== 'time'" label="数据源" prop="data_source_id">
          <el-select
            v-model="form.data_source_id"
            class="full-width"
            placeholder="选择数据源"
            @change="onDataSourceChange"
          >
            <el-option v-for="ds in dataSources" :key="ds.id" :label="ds.name" :value="ds.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="unitRowVisible" label="单位" prop="unit">
          <div class="unit-row">
            <el-select
              v-model="form.unit"
              class="unit-select"
              filterable
              clearable
              allow-create
              default-first-option
              placeholder="选择或手写单位（可空）"
              data-testid="unit-select"
              @change="onUnitChange"
            >
              <el-option-group v-for="g in METRIC_UNIT_GROUPS" :key="g.group" :label="g.group">
                <el-option v-for="o in g.options" :key="o.value" :label="o.label" :value="o.value" />
              </el-option-group>
              <template #empty>
                <div class="unit-select-empty">无匹配的预置单位，输入后回车即可添加自定义单位</div>
              </template>
            </el-select>

            <el-checkbox
              v-model="form.unit_convert"
              class="unit-convert"
              :disabled="!unitConvertible"
              data-testid="unit-convert"
              @change="onUnitConvertChange"
            >
              换算
            </el-checkbox>

            <el-select
              v-if="form.unit_convert"
              v-model="form.unit_base"
              class="unit-base"
              :class="{ 'unit-base-invalid': unitBaseMissing }"
              placeholder="进制"
              data-testid="unit-base-select"
              @change="onUnitBaseChange"
            >
              <el-option label="1000（SI：K/M/G/T）" :value="1000" />
              <el-option label="1024（IEC：Ki/Mi/Gi/Ti）" :value="1024" />
            </el-select>

            <el-tooltip
              placement="top"
              effect="dark"
              :show-after="150"
              append-to-body
              popper-class="unit-tip-popper"
            >
              <template #content>
                <div class="unit-tip" data-testid="unit-tooltip">
                  <p>
                    「换算」只改展示口径：Word 正文、曲线图、AI 摘要与页面四处同步换算；环比与阈值判定仍用原始值。
                  </p>
                  <p>换算后的小数位沿用下方「小数精度」，留空则保留 2 位。</p>
                  <p>Word 模板里若已手写单位请删除，以免重复（会输出成 262.47 Kbps bps）。</p>
                  <p>想改数值本身（它参与环比与阈值判定）请用下方「数值变换」。</p>
                  <p>弹窗内「试算」按已保存的配置计算，改完请先「确定」保存再试算。</p>
                </div>
              </template>
              <el-icon class="unit-tip-icon" data-testid="unit-tooltip-icon">
                <QuestionFilled />
              </el-icon>
            </el-tooltip>
          </div>

          <div class="text-muted form-hint block-hint" data-testid="unit-preview">
            展示口径：{{ unitPreviewText
            }}<span v-if="unitPreviewIsSample">（示例值，试算后按真实值显示）</span>
          </div>
          <div v-if="unitBaseMissing" class="unit-warn" data-testid="unit-base-error">
            自定义单位必须选择换算进制（1000 或 1024）
          </div>
          <div
            v-else-if="form.unit !== '' && !unitConvertible"
            class="unit-warn"
            data-testid="unit-prefix-warn"
          >
            「{{ form.unit }}」已带 K/M/G/T 前缀，换算会自动再加一层前缀（会变成 KKbps）<template
              v-if="unitBaseSuggestion"
              >；想换算请改选「{{ unitBaseSuggestion }}」</template
            >。
          </div>
          <div v-if="form.unit_legacy_notice" class="unit-warn" data-testid="unit-legacy-notice">
            {{ form.unit_legacy_notice }}
          </div>
        </el-form-item>
        <el-form-item v-if="form.metric_type !== 'time'" label="统计周期" prop="period_type">
          <el-select v-model="form.period_type" class="full-width" disabled>
            <el-option label="月报（monthly）" value="monthly" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.metric_type !== 'time'" label="小数精度" prop="display_precision">
          <el-input-number v-model="form.display_precision" :min="0" :max="6" placeholder="留空用 %g" />
          <span class="text-muted form-hint">留空 = 自动（%g）</span>
        </el-form-item>
        <el-form-item label="标签">
          <div class="tag-input">
            <el-tag v-for="(t, i) in form.tags" :key="t" closable @close="removeTag(i)">{{ t }}</el-tag>
            <el-input
              v-model="tagInput"
              placeholder="输入后回车添加，如 核心"
              @keydown.enter.prevent="addTag"
              @blur="addTag"
            />
          </div>
        </el-form-item>

        <template v-if="form.metric_type !== 'time'">
        <div class="form-section">取数配置</div>

        <!-- AIOps：独立级联（模型 → 实例 → 监控项 → 列 → 主键行） -->
        <AiopsScopePicker
          v-if="isAiopsDs"
          :datasource-id="form.data_source_id"
          :model-ids="form.model_ids"
          :instance-ids="form.instance_ids"
          :picks="aiopsPicks"
          :metric-type="form.metric_type"
          @update:model-ids="form.model_ids = $event"
          @update:instance-ids="form.instance_ids = $event"
          @update:picks="aiopsPicks = $event"
          @update:candidates="aiopsCandidates = $event"
          @scope-change="onAiopsScopeChange"
        />

        <!-- Zabbix：原有三级联（路径与升级前完全一致） -->
        <template v-else>
        <el-form-item label="主机组" prop="host_group_ids">
          <el-select
            v-model="form.host_group_ids"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            class="full-width"
            :loading="loadingGroups"
            :disabled="form.data_source_id === null"
            :placeholder="form.data_source_id === null ? '请先选择数据源' : '选择主机组'"
            data-testid="select-host-groups"
            @change="onGroupsChange"
          >
            <el-option v-for="g in groupOptions" :key="g.group_id" :label="g.name" :value="g.group_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="主机" prop="host_ids">
          <el-select
            v-model="form.host_ids"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            class="full-width"
            :loading="loadingHosts"
            :disabled="form.host_group_ids.length === 0"
            :placeholder="form.host_group_ids.length === 0 ? '请先选择主机组' : '选择主机'"
            data-testid="select-hosts"
            @change="onHostsChange"
          >
            <el-option v-for="h in hostOptions" :key="h.host_id" :label="h.name" :value="h.host_id" />
          </el-select>
        </el-form-item>
        <el-form-item
          v-if="
            form.metric_type !== 'curve' &&
            form.metric_type !== 'pie' &&
            (form.metric_type !== 'text' || form.text_mode === 'latest')
          "
          label="监控项"
          prop="item_ids"
        >
          <el-select
            v-model="form.item_ids"
            :multiple="form.metric_type === 'scalar'"
            filterable
            remote
            collapse-tags
            collapse-tags-tooltip
            class="full-width"
            :loading="loadingItems"
            :remote-method="onItemSearch"
            :disabled="form.host_ids.length === 0"
            :placeholder="form.host_ids.length === 0 ? '请先选择主机' : '选择监控项（可输入关键词搜索）'"
            data-testid="select-items"
            @change="onPieItemChange"
          >
            <el-option
              v-for="it in itemOptions"
              :key="it.item_id"
              :label="itemLabel(it)"
              :value="it.item_id"
            />
          </el-select>
        </el-form-item>

        <!-- pie：单选监控项（single-select 不能绑数组，故用独立标量字段） -->
        <el-form-item
          v-if="form.metric_type === 'pie'"
          label="监控项"
          prop="pie_item_id"
        >
          <el-select
            v-model="form.pie_item_id"
            filterable
            remote
            class="full-width"
            :loading="loadingItems"
            :remote-method="onItemSearch"
            :disabled="form.host_ids.length === 0"
            :placeholder="form.host_ids.length === 0 ? '请先选择主机' : '选择监控项（仅 1 个）'"
            data-testid="select-pie-item"
            @change="onPieItemChange"
          >
            <el-option
              v-for="it in itemOptions"
              :key="it.item_id"
              :label="itemLabel(it)"
              :value="it.item_id"
            />
          </el-select>
        </el-form-item>
        </template>
        <!-- /Zabbix 三级联 -->

        <!-- curve：每条曲线独立指定 item / 展示名 / 数值变换 -->

        <template v-if="form.metric_type === 'curve'">
          <div
            v-if="isAiopsDs && !aiopsCandidates.length && form.instance_ids.length"
            class="form-hint"
          >
            该实例暂无可选监控项 —— 若要取表格类数据，请先在上方「监控项」里选列与主键行
          </div>
          <div class="form-section">曲线配置</div>
          <div
            v-for="(row, idx) in form.series"
            :key="idx"
            class="series-row"
            :data-testid="`series-row-${idx}`"
          >
            <el-select
              v-model="row.item_id"
              filterable
              remote
              class="series-item"
              placeholder="监控项"
              :loading="loadingItems"
              :remote-method="onSeriesSearch"
              data-testid="series-item"
              @change="onSeriesItemChange(row)"
            >
              <el-option
                v-for="it in seriesOptionSource"
                :key="it.item_id"
                :label="itemLabel(it)"
                :value="it.item_id"
              />
            </el-select>
            <el-input v-model="row.display_name" placeholder="显示名称" class="series-name" />
            <el-input-number v-model="row.mul" :step="0.001" placeholder="mul" class="series-num" />
            <el-input-number v-model="row.add" :step="0.001" placeholder="add" class="series-num" />
            <el-button link type="danger" @click="removeSeriesRow(idx)">删除</el-button>
          </div>
          <el-button data-testid="add-series" @click="addSeriesRow">添加曲线</el-button>
          <div class="text-muted form-hint block-hint">
            每条曲线独立取一个监控项；数值 = 原始值 × mul + add
          </div>
        </template>

        <!-- pie：单 item + 适用性检测 + UP/DOWN 归类 -->
        <template v-if="form.metric_type === 'pie'">
          <div class="form-section">饼图配置</div>
          <el-form-item label="适用性">
            <el-button
              type="primary"
              plain
              :loading="pieChecking"
              :disabled="!form.pie_item_id"
              data-testid="pie-check-btn"
              @click="checkPieSuitability"
            >
              检测适用性
            </el-button>
            <span class="text-muted form-hint">基于近 7 天数据判断该监控项是否适合做占比</span>
          </el-form-item>

          <el-alert
            v-if="pieSuitability"
            :type="pieSuitability.suitable === true ? 'success' : pieSuitability.suitable === 'unknown' ? 'warning' : 'error'"
            show-icon
            :closable="false"
            class="pie-result"
            data-testid="pie-suitability"
          >
            <template #title>
              {{
                pieSuitability.suitable === true
                  ? '适合做 UP/DOWN 占比分析'
                  : pieSuitability.suitable === 'unknown'
                    ? '无法自动判定，请人工确认'
                    : '不建议做占比分析'
              }}
              （置信度：{{ pieSuitability.confidence }}）
            </template>
            <div class="pie-reason">{{ pieSuitability.reason }}</div>
            <div v-if="pieSuitability.distinct_values.length" class="text-muted form-hint">
              取值：{{ pieSuitability.distinct_values.join('、') }}
            </div>
            <div v-if="pieSuitability.valuemap && Object.keys(pieSuitability.valuemap).length" class="text-muted form-hint">
              value map：{{ Object.entries(pieSuitability.valuemap).map(([k, v]) => `${k}=${v}`).join('、') }}
            </div>
            <el-checkbox
              v-if="pieSuitability.suitable === false"
              v-model="form.pie_ack_risk"
              class="pie-ack"
              data-testid="pie-ack-risk"
            >
              我了解风险，仍要使用该监控项
            </el-checkbox>
          </el-alert>

          <el-form-item label="UP 取值">
            <div class="tag-input">
              <el-tag
                v-for="(v, i) in form.pie_up"
                :key="`up-${v}`"
                closable
                @close="form.pie_up.splice(i, 1)"
              >{{ v }}</el-tag>
              <el-input
                v-model="pieUpInput"
                placeholder="如 1，回车添加"
                data-testid="pie-up-input"
                @keydown.enter.prevent="addPieValue('up')"
              />
            </div>
          </el-form-item>
          <el-form-item label="DOWN 取值">
            <div class="tag-input">
              <el-tag
                v-for="(v, i) in form.pie_down"
                :key="`down-${v}`"
                closable
                @close="form.pie_down.splice(i, 1)"
              >{{ v }}</el-tag>
              <el-input
                v-model="pieDownInput"
                placeholder="如 0，回车添加"
                data-testid="pie-down-input"
                @keydown.enter.prevent="addPieValue('down')"
              />
            </div>
          </el-form-item>
          <el-form-item label="未归类值">
            <el-select v-model="form.pie_unknown_mode" class="full-width">
              <el-option label="排除（不计入分母）" value="exclude" />
              <el-option label="报错（严格模式）" value="error" />
            </el-select>
          </el-form-item>
        </template>
        </template>
        <!-- /取数配置（curve / pie 区块随metric_type 切换，时间类型不取数） -->

        <template v-if="form.metric_type === 'time'">
          <div class="form-section">时间格式</div>
          <el-form-item label="可用模块">
            <div class="time-palette" data-testid="time-palette">
              <el-button
                v-for="m in TIME_MODULES"
                :key="`pal-${m}`"
                size="small"
                class="time-chip"
                :data-testid="`time-module-${m}`"
                @click="addTimeModule(m)"
              >
                {{ m }}
              </el-button>
            </div>
            <span class="text-muted form-hint">点击模块加入格式（可重复点击）</span>
          </el-form-item>
          <el-form-item label="格式" prop="time_format_modules">
            <div class="time-format" data-testid="time-format">
              <el-tag
                v-for="(m, i) in timeForm.format_modules"
                :key="`fmt-${i}-${m}`"
                class="time-fmt-tag"
                closable
                :data-testid="`time-fmt-${i}`"
                @close="removeTimeModule(i)"
              >
                {{ m }}
              </el-tag>
              <span v-if="!timeForm.format_modules.length" class="text-muted">未选择模块</span>
            </div>
            <span class="text-muted form-hint">
              顺序即输出顺序；需要改顺序时删除对应模块后重新点击添加
            </span>
          </el-form-item>
          <el-form-item label="补零">
            <el-switch
              v-model="timeForm.zero_pad"
              active-text="补零（2026-09）"
              inactive-text="不补零（2026-9）"
              data-testid="time-zero-pad"
            />
          </el-form-item>

          <div class="form-section">时间偏移</div>
          <el-form-item label="偏移方式" prop="time_offset_mode">
            <el-radio-group
              v-model="timeForm.offset_mode"
              data-testid="time-offset-mode"
              @change="onTimeOffsetModeChange"
            >
              <el-radio label="manual">手动偏移</el-radio>
              <el-radio label="quick">快速偏移</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="timeForm.offset_mode === 'manual'" label="手动偏移">
            <div class="time-offset-row">
              <el-input-number
                v-model="timeForm.offset_value"
                :step="1"
                :min="-1000"
                :max="1000"
                controls-position="right"
                class="time-offset-number"
                data-testid="time-offset-value"
              />
              <el-select
                v-model="timeForm.offset_unit"
                class="time-offset-unit"
                data-testid="time-offset-unit"
              >
                <el-option label="天" value="day" />
                <el-option label="月" value="month" />
              </el-select>
            </div>
          </el-form-item>
          <el-form-item v-else label="快速偏移" prop="time_quick_offset">
            <el-select
              v-model="timeForm.quick_offset"
              class="time-quick-select"
              placeholder="选择"
              data-testid="time-quick-offset"
            >
              <el-option
                v-for="o in TIME_QUICK_OPTIONS"
                :key="o.value"
                :label="o.label"
                :value="o.value"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="实时示例">
            <div class="time-preview" data-testid="time-preview">
              <span class="time-preview-value">{{ timePreview.text || '（空格式）' }}</span>
            </div>
          </el-form-item>
        </template>

        <template v-if="form.metric_type === 'scalar'">
        <div class="form-section">计算配置</div>
        <el-form-item label="聚合方式" prop="agg">
          <el-select v-model="form.agg" class="full-width">
            <el-option label="平均（avg）" value="avg" />
            <el-option label="求和（sum）" value="sum" />
            <el-option label="最大（max）" value="max" />
            <el-option label="最小（min）" value="min" />
          </el-select>
        </el-form-item>
        <el-form-item label="对比方式" prop="compare">
          <el-select v-model="form.compare" class="full-width">
            <el-option label="无对比（none）" value="none" />
            <el-option label="环比（mom）" value="mom" />
            <el-option label="同比（yoy）" value="yoy" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.compare !== 'none'" label="回溯周期" prop="baseline_lookback">
          <el-input-number v-model="form.baseline_lookback" :min="1" />
          <span class="text-muted form-hint">mom 通常为 1，yoy 通常为 12</span>
        </el-form-item>

        <div class="form-section">数值变换</div>
        <!-- mul / add 是同一个公式的两半，放同一行对照填写（原先上下两行纯浪费纵向空间） -->
        <div class="transform-row">
          <el-form-item label="乘以 mul" prop="transform_mul">
            <el-input-number v-model="form.transform_mul" :step="0.001" data-testid="transform-mul" />
          </el-form-item>
          <el-form-item label="加上 add" prop="transform_add">
            <el-input-number v-model="form.transform_add" :step="0.001" data-testid="transform-add" />
          </el-form-item>
        </div>
        <div class="text-muted form-hint block-hint">最终值 = 原始值 × mul + add（如 bps → Mbps：mul=0.001）。注意：变换会改**事实层数值**，环比与显著波动判定都基于它；只是想换个单位显示（如 bps → Kbps）请用上方「单位」一行的「换算」。</div>
        </template>

        <template v-if="form.metric_type === 'text'">
          <div class="form-section">取值方式</div>
          <el-form-item label="模式" prop="text_mode">
            <el-radio-group
              v-model="form.text_mode"
              data-testid="text-mode-group"
              @change="onTextModeChange"
            >
              <el-radio label="latest">最新值（取所选监控项最新原始值）</el-radio>
              <el-radio label="fixed">固定文本（手填）</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="form.text_mode === 'fixed'" label="固定文本" prop="text_fixed_value">
            <el-input
              v-model="form.text_fixed_value"
              type="textarea"
              :rows="3"
              placeholder="如：本月网络运行平稳，无异常"
              data-testid="text-fixed-value"
            />
          </el-form-item>
        </template>
      </el-form>

      <template #footer>
        <el-button v-if="editingId !== null" @click="openCompute(editingId, form.display_precision)">试算</el-button>
        <el-button :disabled="submitting" @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="onSubmit">确定</el-button>
      </template>
    </FormDialog>

    <!-- 试算弹窗（宽屏，避免抽屉浪费横向空间、图表被压扁） -->
    <el-dialog
      v-model="drawVisible"
      class="compute-dialog"
      title="指标试算"
      width="min(1180px, 94vw)"
      top="4vh"
      :close-on-click-modal="false"
      :show-close="!computing"
      :close-on-press-escape="!computing"
      destroy-on-close
    >
      <el-form class="compute-form" label-position="left" @submit.prevent data-testid="compute-dialog">
        <el-form-item label="统计周期">
          <PeriodPicker v-model="drawPeriod" testid="compute-period" />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            data-testid="compute-submit"
            :loading="computing"
            @click="doCompute"
          >
            试算
          </el-button>
          <span v-if="computing" class="text-muted compute-hint">计算中，约需数秒…</span>
        </el-form-item>
      </el-form>

      <TestResultAlert v-if="computeError" :success="false" :message="computeError" class="drawer-alert" />

      <template v-if="fact">
        <div v-if="factShape(fact) === 'scalar'" class="stat-grid">
          <div class="stat-card">
            <div class="stat-label">本期值</div>
            <div class="stat-value" style="color: var(--facts-color)">
              {{ formatFactValue(factDisplayNumber(fact) ?? 0, fact.display_precision ?? drawPrecision) }}<span class="stat-unit">{{ factDisplayUnit(fact) }}</span>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-label">基线值</div>
            <div class="stat-value">
              {{ factDisplayBaseline !== null ? formatFactValue(factDisplayBaseline, fact.display_precision ?? drawPrecision) : '—' }}<span v-if="factDisplayBaseline !== null" class="stat-unit">{{ factDisplayUnit(fact) }}</span>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-label">环比 / 同比</div>
            <div class="stat-value">
              <span :class="['soft-tag', `soft-tag--${changePctClass(fact.change_pct)}`]">
                {{ formatChangePct(fact.change_pct) }}
              </span>
            </div>
          </div>
        </div>
        <div v-if="scalarRawHint" class="text-muted form-hint block-hint" data-testid="scalar-raw-hint">
          原始值（未换算）：{{ scalarRawHint }} —— 环比与阈值判定基于此口径
        </div>

        <div
          v-else-if="factShape(fact) === 'series'"
          class="chart-box"
          data-testid="fact-chart"
        >
          <!-- 空数据：与后端 chart_renderer 的"暂无数据"占位图保持一致 -->
          <div v-if="curveEmpty" class="chart-placeholder" data-testid="curve-empty-placeholder">
            暂无数据
            <span class="chart-placeholder-hint">该监控项在本统计周期内没有趋势数据，请切换周期</span>
          </div>
          <div v-else ref="curveChartEl" class="chart-host" data-testid="curve-chart" />
        </div>
        <div
          v-else-if="factShape(fact) === 'pie'"
          class="chart-box"
          data-testid="fact-chart"
        >
          <div v-if="pieEmpty" class="chart-placeholder" data-testid="pie-empty-placeholder">
            暂无数据
            <span class="chart-placeholder-hint">该监控项在本统计周期内没有可统计的小时数</span>
          </div>
          <div v-else ref="pieChartEl" class="chart-host chart-host--pie" data-testid="pie-chart" />
        </div>
        <div v-else class="value-box" data-testid="fact-value-box">
          <div class="value-box-label">
            {{ fact.value_shape === 'text' ? '文本内容' : '结构化值' }}
            <span class="soft-tag soft-tag--info">{{ fact.value_shape }}</span>
          </div>
          <div class="value-box-text">{{ factValueText(fact) }}</div>
        </div>

        <el-divider />
        <div class="evidence">
          <div class="evidence-item">
            <div class="evidence-label">来源系统</div>
            <div class="evidence-value">{{ fact.source }}</div>
          </div>
          <div class="evidence-item">
            <div class="evidence-label">原始对象</div>
            <div class="evidence-value">{{ fact.object }}</div>
          </div>
          <div class="evidence-item">
            <div class="evidence-label">计算公式</div>
            <div class="evidence-value">{{ fact.formula }}</div>
          </div>
          <div class="evidence-item">
            <div class="evidence-label">统计周期</div>
            <div class="evidence-value">{{ fact.period }}</div>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.action-cell {
  display: flex;
  align-items: center;
  gap: var(--space-3);

  .el-button {
    margin-left: 0;
  }
}

.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.filter-tags {
  width: 300px;
}

.filter-name {
  width: 200px;
}

.filter-status {
  width: 120px;
}

.tag-cell {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}

.form-section {
  margin: var(--space-3) 0 var(--space-2);
  padding-left: var(--space-3);
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--color-text-secondary);
  border-left: 2px solid var(--color-primary);
}

.full-width {
  width: 100%;
}

.form-hint {
  margin-left: var(--space-2);
  font-size: var(--font-xs);
}

.tag-input {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
  width: 100%;
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--input-radius);
  background-color: var(--color-bg-elevated);
}

.tag-input .el-input {
  flex: 1;
  min-width: 160px;
}

.type-alert {
  margin-bottom: var(--space-3);
}

.block-hint {
  margin: 0 0 var(--space-3) var(--space-2);
}

/* 数值变换：mul / add 同行 */
.transform-row {
  display: flex;
  gap: var(--space-4);
}

.transform-row :deep(.el-form-item) {
  flex: 1 1 0;
  min-width: 0;
}

/* ---- 指标「单位」行：单位下拉 + 换算勾选 + 进制 + tooltip（Grafana standard options 风格） ---- */
.unit-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.unit-select {
  width: 220px;
  min-width: 160px;
}

.unit-select-empty {
  padding: var(--space-2) var(--space-3);
  color: var(--color-text-tertiary);
  font-size: var(--font-xs);
}

.unit-convert :deep(.el-checkbox__label) {
  color: var(--color-text-secondary);
  font-size: var(--font-sm);
}

.unit-base {
  width: 188px;
}

.unit-base-invalid :deep(.el-select__wrapper) {
  --el-border-color: var(--color-danger);
  box-shadow: 0 0 0 1px var(--color-danger) inset;
}

.unit-tip-icon {
  color: var(--color-text-quaternary);
  font-size: var(--font-md);
  cursor: pointer;
  transition: color 0.15s ease;
}

.unit-tip-icon:hover {
  color: var(--color-primary);
}

.unit-warn {
  margin: 0 0 var(--space-3) var(--space-2);
  color: var(--color-danger);
  font-size: var(--font-xs);
  line-height: 1.6;
}

.drawer-alert {
  margin-top: var(--space-4);
}

.series-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}

.series-item {
  width: 220px;
}

.series-name {
  width: 150px;
}

.series-num {
  width: 110px;
}

.pie-result {
  margin-bottom: var(--space-3);
}

.pie-reason {
  margin-top: var(--space-1);
  font-size: var(--font-sm);
}

.pie-ack {
  margin-top: var(--space-2);
}

/* ---- 时间类型：格式模块 / 偏移 / 实时示例 ---- */
.time-palette {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  margin-bottom: var(--space-1);
}

.time-chip {
  margin: 0;
}

.time-format {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-1);
  width: 100%;
  min-height: 40px;
  padding: var(--space-2);
  border: 1px dashed var(--border-color, #cbd5e1);
  border-radius: 4px;
}

/* 格式模块用等宽字体，"2026-09" 之类更好对齐辨认 */
.time-fmt-tag {
  font-family: var(--font-mono, monospace);
}

/* 偏移量与单位下拉同一行（不换行占满整行） */
.time-offset-row {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: var(--space-2);
}

.time-offset-number {
  width: 130px;
}

.time-offset-unit {
  width: 80px;
}

.time-quick-select {
  width: 160px;
}

.time-preview-value {
  font-family: var(--font-mono, monospace);
  font-size: 18px;
  font-weight: 600;
  color: var(--facts-color, #0f172a);
}

.chart-box {
  margin-top: var(--space-4);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--card-radius);
  background-color: var(--card-bg);
}

.compute-form {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

.compute-form :deep(.el-form-item) {
  margin-bottom: 0;
}

.compute-hint {
  margin-left: var(--space-2);
  font-size: 13px;
}

/* 空数据占位：与后端 PNG 占位图同款（灰底 + 居中提示） */
.chart-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  width: 100%;
  height: 360px;
  border: 1px solid var(--border-color, #cbd5e1);
  border-radius: 4px;
  background: #f1f5f9;
  color: #64748b;
  font-size: 14px;
}

.chart-placeholder-hint {
  font-size: 12px;
  color: #94a3b8;
}

/* 试算弹窗：内容区可滚动，图表尽量大 */
.compute-dialog :deep(.el-dialog__body) {
  max-height: 76vh;
  overflow-y: auto;
}

.chart-host {
  width: 100%;
  height: 360px;
}

.chart-host--pie {
  height: 340px;
}

.chart-empty {
  text-align: center;
  padding-top: var(--space-4);
}

.value-box {
  margin-top: var(--space-4);
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--card-radius);
  background-color: var(--card-bg);
}

.value-box-label {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-xs);
  color: var(--color-text-tertiary);
}

.value-box-text {
  margin-top: var(--space-2);
  font-size: var(--font-lg);
  font-weight: 600;
  color: var(--color-text-primary);
  word-break: break-all;
  white-space: pre-wrap;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-3);
  margin-top: var(--space-4);
}

.stat-card {
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--card-radius);
  background-color: var(--card-bg);
}

.stat-label {
  font-size: var(--font-xs);
  color: var(--color-text-tertiary);
}

.stat-value {
  margin-top: var(--space-2);
  font-size: var(--font-xl);
  font-weight: 600;
  color: var(--color-text-primary);
}

.stat-unit {
  margin-left: var(--space-1);
  font-size: var(--font-sm);
  font-weight: 400;
  color: var(--color-text-tertiary);
}

.evidence-item {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  font-size: var(--font-sm);
}

.evidence-label {
  flex-shrink: 0;
  width: 80px;
  color: var(--color-text-tertiary);
}

.evidence-value {
  color: var(--color-text-primary);
  word-break: break-all;
}

:deep(.danger-item) {
  color: var(--color-danger);
}
</style>

<!--
  单位提示气泡（tooltip）的内容由 el-tooltip teleport 到 body，scoped 样式命中不了，
  故这里用 popper-class 收口，并且只作用于这一个气泡
-->
<style lang="scss">
.unit-tip-popper {
  max-width: 380px;
}

.unit-tip-popper .unit-tip p {
  margin: 0 0 var(--space-2);
  font-size: var(--font-xs);
  line-height: 1.6;
}

.unit-tip-popper .unit-tip p:last-child {
  margin-bottom: 0;
}
</style>
