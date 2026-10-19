import { http } from './http'

export type PushRecordStatus = 'success' | 'failed'
/**
 * push / retry_push = 推送报告附件；
 * confirmation = 确认邮件（含"重新发送确认邮件"）；
 * reminder = 24h 未确认的催办提醒。
 */
export type PushRecordAction = 'push' | 'retry_push' | 'confirmation' | 'reminder'

export interface PushRecord {
  id: number
  report_id: number
  /** 报告名（模板名_周期） */
  report_name: string
  /** 该报告第几次发送（含确认 / 催办邮件） */
  attempt: number
  action: PushRecordAction
  status: PushRecordStatus
  recipients: string[]
  recipient_count: number
  attachment_name: string | null
  duration_ms: number | null
  error: string | null
  actor_id: number | null
  created_at: string
}

export interface PushRecordList {
  total: number
  items: PushRecord[]
}

export interface PushRecordQuery {
  report_id?: number | null
  status?: PushRecordStatus | null
  action?: PushRecordAction | null
  keyword?: string | null
  limit?: number
  /** 分页偏移；不传则由后端返回前 limit 条 */
  offset?: number
}

export const pushRecordApi = {
  list(params: PushRecordQuery) {
    return http.get<PushRecordList>('/push-records', { params })
  },
}
