import type { CalcFormula, MetricType, MetricFact, QueryConfig, QueryConfigResolved, ValueShape } from '@/api/types'

/** 取数范围名称解析结果（详情接口返回；列表页无解析时传 undefined） */
export interface ScopeNames {
  host_groups?: Record<string, string>
  hosts?: Record<string, string>
  items?: Record<string, string>
}

/** 指标类型文案 */
export const METRIC_TYPE_LABEL: Record<MetricType, string> = {
  scalar: '数值',
  curve: '曲线图',
  pie: '饼图',
  text: '文本',
  time: '时间',
}

/** 指标类型软标签色：scalar 蓝 / curve 青 / pie 橙 / text 灰 / time 紫(accent) */
export function metricTypeClass(
  t: MetricType,
): 'info' | 'success' | 'warning' | 'neutral' | 'accent' {
  switch (t) {
    case 'scalar':
      return 'info'
    case 'curve':
      return 'success'
    case 'pie':
      return 'warning'
    case 'text':
      return 'neutral'
    case 'time':
      return 'accent'
  }
}

/** 当前开放的指标类型（time 不取数，仅用当前时间 + 偏移 + 自定义格式） */
export const TYPE_ENABLED: MetricType[] = ['scalar', 'curve', 'pie', 'text', 'time']

/**
 * 取数范围摘要（按指标类型分型）：
 * - scalar/text：组 / 主机 / N 个监控项
 * - time：返回 timeSummary（时间类型不取数）
 * - curve：组 / 主机 / N 条曲线
 * - pie：组 / 主机 / 单监控项
 * 有名称时展示名称（用户全程看不到 id），无名称时降级展示 id。
 */
export function formatScope(
  qc: QueryConfig,
  names: ScopeNames = {},
  metricType: MetricType = 'scalar',
  timeSummary?: string,
): string {
  // 时间类型与数据源无关，没有取数范围
  if (metricType === 'time') return timeSummary ?? '按当前时间计算'
  const groups = (qc.host_group_ids ?? []).map((g) => names.host_groups?.[g] ?? g)
  const hosts = (qc.host_ids ?? []).map((h) => names.hosts?.[h] ?? h)
  const parts: string[] = []
  if (groups.length) parts.push(groups.join('、'))
  if (hosts.length) parts.push(hosts.join('、'))

  if (metricType === 'curve') {
    const series = qc.series ?? []
    if (series.length) {
      const shown = series
        .slice(0, 2)
        .map((s) => s.display_name || names.items?.[s.item_id] || s.item_id)
      parts.push(
        series.length > 2 ? `${shown.join('、')} 等 ${series.length} 条曲线` : `${shown.join('、')}（${series.length} 条曲线）`,
      )
    }
  } else if (metricType === 'pie') {
    const it = (qc.item_ids ?? [])[0]
    if (it) parts.push(names.items?.[it] ?? it)
  } else {
    const items = (qc.item_ids ?? []).map((i) => names.items?.[i] ?? i)
    if (items.length) {
      const shown = items.slice(0, 2).join('、')
      parts.push(items.length > 2 ? `${shown} 等 ${items.length} 项` : shown)
    }
  }
  return parts.length ? parts.join(' / ') : '—'
}

/** 由详情接口的 query_config_resolved 生成 ScopeNames 映射 */
export function scopeNamesFrom(
  resolved: QueryConfigResolved | null | undefined,
): ScopeNames {
  if (!resolved) return {}
  return {
    host_groups: Object.fromEntries(resolved.host_groups.map((g) => [g.id, g.name])),
    hosts: Object.fromEntries(resolved.hosts.map((h) => [h.id, h.name])),
    items: Object.fromEntries(resolved.items.map((i) => [i.id, i.name])),
  }
}

/** 标签折叠：超过 limit 条折叠为 "+N"（hover 展示全部） */
export function foldTags<T>(tags: T[], limit = 3): { shown: T[]; rest: number } {
  if (tags.length <= limit) return { shown: tags, rest: 0 }
  return { shown: tags.slice(0, limit), rest: tags.length - limit }
}

/** 聚合对比摘要：如 "avg · 环比 mom(1)"；compare=none 时只显示 agg */
export function formatCompare(calc: CalcFormula): string {
  if (calc.compare === 'none') {
    return calc.agg
  }
  const label = calc.compare === 'mom' ? '环比' : '同比'
  return `${calc.agg} · ${label} ${calc.compare}(${calc.baseline_lookback ?? 1})`
}

export type ChangeClass = 'success' | 'warning' | 'neutral'

/** 涨跌分类（阈值 ±10%）：涨=warning，跌=success，平稳=neutral */
export function changePctClass(pct: number | null, threshold = 10): ChangeClass {
  if (pct === null) return 'neutral'
  if (pct > threshold) return 'warning'
  if (pct < -threshold) return 'success'
  return 'neutral'
}

/** 涨跌幅展示：正数带 +，保留两位小数，null 显示 — */
export function formatChangePct(pct: number | null): string {
  if (pct === null) return '—'
  const sign = pct > 0 ? '+' : ''
  return `${sign}${pct.toFixed(2)}%`
}

/**
 * 试算预览的指标值格式化（区别于报告产物）：
 * display_precision 为整数 N -> 固定 N 位小数；null -> 保留原始值。
 * 报告产物由后端渲染时按 display_precision 处理，前端不再重复格式化。
 */
export function formatFactValue(value: number, precision: number | null): string {
  if (precision === null) return String(value)
  return value.toFixed(precision)
}

/**
 * 容忍字段缺失的 fact 结构。
 * 索引签名是为了绕开 TS 的 weak-type 检测：历史 facts_json 完全没有
 * value_shape 字段，这正是下面三个函数要兼容的场景（表格行还是 any）。
 */
type FactLike = { value_shape?: ValueShape | null; [k: string]: unknown }

/** fact 的实际形态（历史数据缺 value_shape 时按 scalar） */
export function factShape(fact: FactLike): ValueShape {
  return fact.value_shape ?? 'scalar'
}

/** fact.value 的标量部分；非标量形态返回 null */
export function factNumber(fact: FactLike & { value: MetricFact['value'] }): number | null {
  return factShape(fact) === 'scalar' && typeof fact.value === 'number' ? fact.value : null
}

/**
 * fact 的展示数值（换算后优先）：
 * display_value 由后端 calc_engine 按指标 unit_config 算出；缺省（未配置换算 / 旧报告
 * 快照）回退原始 value。事实层数值不受换算影响，需要原始值时仍用 factNumber。
 */
export function factDisplayNumber(
  fact: FactLike & { value: MetricFact['value']; display_value?: number | null },
): number | null {
  if (factShape(fact) !== 'scalar') return null
  if (typeof fact.display_value === 'number') return fact.display_value
  return typeof fact.value === 'number' ? fact.value : null
}

/** fact 的展示单位（换算后优先）：display_unit 缺省时回退原始 unit */
export function factDisplayUnit(
  fact: FactLike & { unit?: string | null; display_unit?: string | null },
): string {
  if (typeof fact.display_unit === 'string' && fact.display_unit !== '') {
    return fact.display_unit
  }
  return fact.unit ?? ''
}

/**
 * fact 值的可读文本（列表/详情通用）：
 * - scalar -> 按展示口径格式化的数值（换算后优先，见 factDisplayNumber）
 * - text   -> payload.text
 * - pie    -> "UP x% / DOWN y%"
 * - series -> "N 条曲线 / M 个点"（阶段 1 不渲染图表）
 */
export function factValueText(
  fact: FactLike & {
    value: MetricFact['value']
    display_precision?: number | null
    display_value?: number | null
  },
): string {
  const shape = factShape(fact)
  const payload = (typeof fact.value === 'object' && fact.value !== null ? fact.value : {}) as Record<string, unknown>
  if (shape === 'scalar') {
    const n = factDisplayNumber(fact)
    return n === null ? '—' : formatFactValue(n, fact.display_precision ?? null)
  }
  if (shape === 'text') {
    const t = payload.text
    return typeof t === 'string' && t !== '' ? t : '—'
  }
  if (shape === 'pie') {
    const up = typeof payload.up_pct === 'number' ? payload.up_pct : null
    const down = typeof payload.down_pct === 'number' ? payload.down_pct : null
    if (up === null || down === null) return '—'
    return `UP ${up.toFixed(1)}% / DOWN ${down.toFixed(1)}%`
  }
  const series = Array.isArray(payload.series) ? payload.series : []
  const points = series.reduce(
    (sum, s) => sum + (((s as { points?: unknown[] })?.points?.length) ?? 0),
    0,
  )
  return series.length ? `${series.length} 条曲线 / ${points} 个点` : '—'
}
