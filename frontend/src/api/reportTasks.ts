import { http } from './http'
import type {
  ConfirmPreview,
  ConfirmResult,
  ReportTaskCreate,
  ReportTaskRead,
  ReportTaskRunResult,
  ReportTaskUpdate,
} from './types'

export const reportTaskApi = {
  list() {
    return http.get<ReportTaskRead[]>('/report-tasks')
  },
  get(id: number) {
    return http.get<ReportTaskRead>(`/report-tasks/${id}`)
  },
  create(payload: ReportTaskCreate) {
    return http.post<ReportTaskRead>('/report-tasks', payload)
  },
  update(id: number, payload: ReportTaskUpdate) {
    return http.put<ReportTaskRead>(`/report-tasks/${id}`, payload)
  },
  /** 启用/停用（停用会移除调度 job，已产生的实例不受影响） */
  setActive(id: number, active: boolean) {
    return http.post<ReportTaskRead>(`/report-tasks/${id}/active`, null, {
      params: { active },
    })
  },
  /** 物理删除：仅允许 is_active=false 且从未执行过的任务 */
  remove(id: number) {
    return http.delete<void>(`/report-tasks/${id}`)
  },
  /** 手动触发一次（默认同样走确认流程，用于真实演练）；立即返回 */
  run(id: number, period?: string) {
    return http.post<ReportTaskRunResult>(`/report-tasks/${id}/run`, null, {
      params: period ? { period } : {},
    })
  },
}

/**
 * 确认接口（**公开**，无需登录）：凭邮件里的一次性 token。
 * 不走带 Authorization 头的 http 实例（确认人可能没有平台账号）。
 */
export const confirmApi = {
  preview(reportId: number, token: string) {
    return http.get<ConfirmPreview>(`/confirm/${reportId}`, { params: { token } })
  },
  confirm(reportId: number, token: string) {
    return http.post<ConfirmResult>(`/confirm/${reportId}`, { token })
  },
}