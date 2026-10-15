import type { MetricFact, ReportStatus } from '@/api/types'

/** 报告可用的操作（状态机导出） */
export type ReportAction =
  | 'download' // 下载 docx
  | 'resendConfirmation' // 重新发送确认邮件（超期后）
  | 'confirmByAdmin' // 管理员代确认（平台内直接放行，无需等确认人点链接）
  | 'retryPush' // 重试推送（confirmed 但推送失败）
  | 'regenerate' // failed -> 重新生成

/** 状态中文标签 */
export const STATUS_LABEL: Record<ReportStatus, string> = {
  pending: '待生成',
  generating: '生成中',
  pending_confirmation: '待确认',
  confirmed: '已确认',
  pushed: '已推送',
  push_failed: '推送失败',
  failed: '生成失败',
}

/** 状态软标签色（Design Token §1.2 软标签）：pending=neutral, generating=info,
 *  pending_confirmation=warning, confirmed=info, pushed=success,
 *  push_failed=danger, failed=danger */
export function statusSoftClass(status: ReportStatus): string {
  switch (status) {
    case 'pending':
      return 'neutral'
    case 'generating':
    case 'confirmed':
      return 'info'
    case 'pending_confirmation':
      return 'warning'
    case 'pushed':
      return 'success'
    case 'push_failed':
    case 'failed':
      return 'danger'
    default:
      return 'neutral'
  }
}

/** pushed 状态带成功图标 */
export function statusHasIcon(status: ReportStatus): boolean {
  return status === 'pushed'
}

/**
 * 状态 -> 可用操作列表（状态机）。
 * - pending/generating：无（后台在跑）
 * - pending_confirmation：可重新发送确认邮件（尤其超期后）
 * - confirmed：可重试推送（补偿或失败后）
 * - pushed：仅下载
 * - push_failed：重试推送 + 下载
 * - failed：重新生成
 */
export function statusActions(status: ReportStatus): ReportAction[] {
  switch (status) {
    case 'pending':
    case 'generating':
      return []
    case 'pending_confirmation':
      // 待确认：管理员可代确认直接放行，或重发邮件让确认人自己点
      return ['confirmByAdmin', 'resendConfirmation', 'download']
    case 'confirmed':
      return ['retryPush', 'download']
    case 'pushed':
      return ['download']
    case 'push_failed':
      return ['retryPush', 'download']
    case 'failed':
      return ['regenerate', 'download']
    default:
      return []
  }
}

/** 操作中文标签 */
export const ACTION_LABEL: Record<ReportAction, string> = {
  download: '下载',
  confirmByAdmin: '代确认并发送',
  resendConfirmation: '重新发送确认邮件',
  retryPush: '重试推送',
  regenerate: '重新生成',
}

/** 报告名：模板名 + 统计周期（空格拼接），如 "运维月报模板 2026-08" */
export function reportName(templateName: string, period: string): string {
  return `${templateName} ${period}`
}

/** 来源标签：任务触发给任务名（可跳转），手动给"手动"灰标签 */
export function sourceLabel(item: { task_name?: string | null; source: string }): string {
  return item.task_name ?? '手动'
}

/** 是否"确认链接已过期"（仅 pending_confirmation 有意义） */
export function isConfirmationExpired(item: {
  status: ReportStatus
  expires_at?: string | null
}): boolean {
  if (item.status !== 'pending_confirmation' || !item.expires_at) return false
  return new Date(item.expires_at).getTime() < Date.now()
}

/** facts 中异常指标数：|change_pct| > threshold（默认 10%） */
export function countAbnormalFacts(facts: MetricFact[], threshold = 10): number {
  return facts.filter(
    (f) => f.change_pct !== null && Math.abs(f.change_pct) > threshold,
  ).length
}

/** 审核动作软标签色：generate/generate_ok/generate_fail=info,
 *  confirm=success, push=success, retry_push=warning,
 *  resend_confirmation/reminder_sent=warning, generate_fail=danger */
export function auditActionSoftClass(action: string): string {
  switch (action) {
    case 'generate':
    case 'regenerate':
    case 'generate_ok':
      return 'info'
    case 'confirm':
    case 'confirm_by_admin':
    case 'push':
      return 'success'
    case 'generate_fail':
    case 'push_fail':
      return 'danger'
    case 'retry_push':
    case 'resend_confirmation':
    case 'reminder_sent':
      return 'warning'
    default:
      return 'neutral'
  }
}

/** 审核动作中文标签 */
export function auditActionLabel(action: string): string {
  const map: Record<string, string> = {
    generate: '创建实例',
    regenerate: '重新生成',
    generate_ok: '生成成功',
    generate_fail: '生成失败',
    confirmation_sent: '发送确认邮件',
    resend_confirmation: '重新发送确认邮件',
    reminder_sent: '发送超时提醒',
    confirm: '确认人确认',
    confirm_by_admin: '管理员代确认',
    push: '推送',
    push_fail: '推送失败',
    retry_push: '重试推送',
  }
  return map[action] ?? action
}