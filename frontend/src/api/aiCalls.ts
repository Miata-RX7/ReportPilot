import { http } from './http'
import type { AiCallDetail, AiCallList } from './types'

export interface AiCallQuery {
  /** 按报告过滤（报告生成 / 单段重生成） */
  reportId?: number
  /** 按模板过滤（试跑历史） */
  templateId?: number
  /** 按摘要槽位过滤 */
  slot?: string
  kind?: 'generate' | 'regenerate' | 'preview'
  limit?: number
}

/**
 * AI 调用留痕查询（只读）。
 *
 * 写入在后端 `services/ai_trace`，由 `ai_insight.generate_summary` 这一唯一
 * 出口触发；本文件只负责取回「每次请求 AI 的参数、prompt 与响应」。
 */
export const aiCallApi = {
  /** 倒序列出记录：默认最近 20 条，不含请求 / 响应正文 */
  list(params: AiCallQuery = {}) {
    const q: Record<string, string | number> = {}
    if (params.reportId !== undefined) q.report_id = params.reportId
    if (params.templateId !== undefined) q.template_id = params.templateId
    if (params.slot) q.slot = params.slot
    if (params.kind) q.kind = params.kind
    if (params.limit !== undefined) q.limit = params.limit
    return http.get<AiCallList>('/ai-calls', { params: q })
  },
  /** 单条详情：完整 messages、模型参数、响应原文与护栏重试 */
  detail(id: number) {
    return http.get<AiCallDetail>(`/ai-calls/${id}`)
  },
}