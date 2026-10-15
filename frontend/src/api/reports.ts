import { http, LONG_TIMEOUT_MS } from './http'
import type {
  AuditRead,
  InsightAIConfig,
  InsightSlot,
  InsightSlotResult,
  MetricFact,
  PushRecord,
  ReportDetail,
  ReportListItem,
  ReportRead,
} from './types'

export interface ReportDetailData extends ReportDetail {
  facts_json: MetricFact[]
  insights_json: Record<string, InsightSlot>
  audits: AuditRead[]
  push_records: PushRecord[]
}

export const reportApi = {
  /** 列表：支持按 status / period / task_id 过滤，limit 取最新 N 条 */
  list(
    params: { status?: string; period?: string; taskId?: number; limit?: number } = {},
  ) {
    const q: Record<string, string | number> = {}
    if (params.status) q.report_status = params.status
    if (params.period) q.period = params.period
    if (params.taskId !== undefined) q.task_id = params.taskId
    if (params.limit !== undefined) q.limit = params.limit
    return http.get<ReportListItem[]>('/reports', { params: q })
  },
  detail(id: number) {
    return http.get<ReportDetailData>(`/reports/${id}`)
  },
  /** 下载 docx：blob 响应，silent，长超时 */
  download(id: number) {
    return http.get(`/reports/${id}/download`, {
      responseType: 'blob',
      silent: true,
      timeout: LONG_TIMEOUT_MS,
    })
  },
  /** failed -> 重新生成（后台执行，202） */
  regenerate(id: number) {
    return http.post<ReportRead>(`/reports/${id}/regenerate`)
  },
  /** 重新发送确认邮件（换新 token、重置过期时间） */
  resendConfirmation(id: number) {
    return http.post<void>(`/reports/${id}/resend-confirmation`)
  },
  /**
   * **管理员代确认**：平台内直接确认并推送，不等确认人点邮件链接。
   * 允许对已过期链接代确认；审计记录写"管理员代确认"+ 操作人。
   */
  confirmByAdmin(id: number) {
    return http.post<ReportRead>(`/reports/${id}/confirm`)
  },
  /** push_failed -> 重试推送 */
  retryPush(id: number) {
    return http.post<ReportRead>(`/reports/${id}/retry-push`, {}, { timeout: LONG_TIMEOUT_MS })
  },
  /**
   * 单段摘要重生成：只重算该段并重渲染 docx（facts 用报告快照，不重算指标）。
   * 传 aiConfig 可临时覆盖（改完先试后存），不传则用模板当前绑定。
   */
  regenerateInsight(id: number, slot: string, aiConfig?: InsightAIConfig | null) {
    return http.post<InsightSlotResult>(
      `/reports/${id}/insights/${encodeURIComponent(slot)}/regenerate`,
      { ai_config: aiConfig ?? null },
      { timeout: LONG_TIMEOUT_MS },
    )
  },
}