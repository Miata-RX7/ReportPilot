import type { BindingRead, TemplatePermission } from '@/api/types'

/** 权限判定：edit/manage 可编辑文档与绑定 */
export function canEdit(permission: TemplatePermission | undefined | null): boolean {
  return permission === 'edit' || permission === 'manage'
}

/** 权限判定：仅 manage 可授权/转移属主/删除 */
export function canManage(permission: TemplatePermission | undefined | null): boolean {
  return permission === 'manage'
}

/** 权限文案 */
export function permissionText(permission: TemplatePermission): string {
  return { manage: '管理', edit: '编辑', view: '只读' }[permission] ?? permission
}

/** 权限软标签色：manage=success、edit=info、view=neutral */
export function permissionClass(
  permission: TemplatePermission,
): 'success' | 'info' | 'neutral' {
  if (permission === 'manage') return 'success'
  if (permission === 'edit') return 'info'
  return 'neutral'
}

export interface BindingStats {
  total: number
  bound: number
  unbound: string[]
}

/** 绑定统计：已绑定数 / 总数，未绑定占位符列表（bindings 与 placeholders 的 diff） */
export function bindingStats(
  placeholders: string[],
  bindings: BindingRead[],
): BindingStats {
  const boundSet = new Set(bindings.map((b) => b.placeholder))
  return {
    total: placeholders.length,
    bound: placeholders.filter((p) => boundSet.has(p)).length,
    unbound: placeholders.filter((p) => !boundSet.has(p)),
  }
}

/** 绑定进度软标签类型：全绑=success、部分=warning、无=neutral */
export function bindingProgressClass(
  stats: BindingStats,
): 'success' | 'warning' | 'neutral' {
  if (stats.total === 0) return 'neutral'
  if (stats.bound === stats.total) return 'success'
  if (stats.bound > 0) return 'warning'
  return 'neutral'
}

/** 绑定进度文案：全绑="已全部绑定"、部分="N/M 已绑定"、无="未绑定" */
export function bindingProgressText(stats: BindingStats): string {
  if (stats.total === 0) return '无变量'
  if (stats.bound === stats.total) return '已全部绑定'
  if (stats.bound > 0) return `${stats.bound}/${stats.total} 已绑定`
  return '未绑定'
}


/** 图表类型指标（Word 内以图片渲染，要求变量独占段落） */
export function isChartMetric(metricType: string | undefined | null): boolean {
  return metricType === 'curve' || metricType === 'pie'
}

/**
 * 图表类型指标能否绑定到该占位符。
 * docxtpl 插图要求变量独占段落，否则 140mm 宽的行内图片会挤在文字中间。
 */
export function canBindChartMetric(
  metricType: string | undefined | null,
  isAloneInParagraph: boolean,
): boolean {
  if (!isChartMetric(metricType)) return true
  return isAloneInParagraph
}

/** 非独占占位符的提示文案（绑定矩阵黄色警示） */
export const MIXED_PLACEHOLDER_HINT =
  '该变量与其他内容混排，图表类型指标无法绑定到此处'
