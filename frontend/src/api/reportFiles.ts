import { http, LONG_TIMEOUT_MS } from './http'

/** 上传的本期数据文件（Excel） */
export interface ReportFile {
  id: number
  filename: string
  size_bytes: number
  row_count: number
  columns: string[]
  summary?: ReportSummary | null
}

/** 一段总结（含溯源与校验结果） */
export interface ReportSummary {
  placeholder: string
  prompt: string
  text: string
  categories: SummaryCategory[]
  verification: SummaryVerification
}

/** 一个分类及其证据行 —— 「数字指不指得出具体的行」 */
export interface SummaryCategory {
  name: string
  count: number
  rows: number[]
  match: { type?: string; column?: string; value?: string; keywords?: string[] }
  verified: boolean
  notes: string[]
}

export interface SummaryVerification {
  categories: number
  numbers: number
  verified: boolean
  issues: string[]
}

/** AI 生成的总结草稿（尚未落库） */
export interface SummaryDraft {
  text: string
  categories: SummaryCategory[]
  verification: SummaryVerification
  sheet_name: string
  label_col: string
  row_count: number
}

/**
 * 确认页的数据文件与总结接口（**无需登录**，凭邮件里的一次性 token）。
 */
export const reportFileApi = {
  list(reportId: number, token: string) {
    return http.get<ReportFile[]>(`/report-files/${reportId}/files`, { params: { token } })
  },
  upload(reportId: number, token: string, file: File, placeholder?: string) {
    const fd = new FormData()
    fd.append('token', token)
    fd.append('file', file)
    // **上传时就绑定占位**（一变量一文件）：文件与占位的关系由上传动作确定，
    // 不需要事后用下拉去选 —— 下拉正是「两个文件选到同一占位 -> 静默覆盖」
    // 的来源。
    if (placeholder) fd.append('placeholder', placeholder)
    return http.post<ReportFile>(`/report-files/${reportId}/files`, fd)
  },
  summarize(
    reportId: number,
    fileId: number,
    token: string,
    params: { prompt?: string; sheet_name?: string; label_col?: string },
  ) {
    // 这个接口会**串行跑多次 AI 调用**（每 30 行一块逐块分类，再合成一段），
    // 147 行约 5 块 + 1 次合成 = 6 次往返，可能远超默认的 30 秒。
    // 必须显式用长超时，否则前端先超时、后端还在跑（用户看到「请求超时」
    // 但服务器其实在正常工作）。
    return http.post<SummaryDraft>(`/report-files/${reportId}/files/${fileId}/summary`, null, {
      params: { token, ...params },
      timeout: LONG_TIMEOUT_MS,
    })
  },
}
