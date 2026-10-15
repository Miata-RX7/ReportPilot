import { http, LONG_TIMEOUT_MS } from './http'
import type {
  BindingItem,
  BindingRead,
  EditorConfig,
  GrantItem,
  GrantRead,
  InsightAIConfig,
  InsightPreviewResult,
  TemplateDetail,
  TemplateListItem,
  VersionRead,
} from './types'

export interface BindingsResult {
  bindings: BindingRead[]
  cleared_count: number
}

export const templateApi = {
  list() {
    return http.get<TemplateListItem[]>('/templates')
  },
  detail(id: number) {
    return http.get<TemplateDetail>(`/templates/${id}`)
  },
  upload(name: string, file: File) {
    const form = new FormData()
    form.append('name', name)
    form.append('file', file)
    return http.post<TemplateDetail>('/templates', form)
  },
  putBindings(id: number, bindings: BindingItem[]) {
    return http.put<BindingsResult>(`/templates/${id}/bindings`, { bindings })
  },
  remove(id: number) {
    return http.delete<void>(`/templates/${id}`)
  },
  /** 原始 docx（blob），用于弹窗内 docx-preview 渲染 */
  file(id: number) {
    return http.get(`/templates/${id}/file`, { responseType: 'blob', silent: true })
  },
  versions(id: number) {
    return http.get<VersionRead[]>(`/templates/${id}/versions`)
  },
  activateVersion(id: number, versionId: number) {
    return http.post<VersionRead>(`/templates/${id}/versions/${versionId}/activate`)
  },
  grants(id: number) {
    return http.get<GrantRead[]>(`/templates/${id}/grants`)
  },
  putGrants(id: number, grants: GrantItem[]) {
    return http.put<GrantRead[]>(`/templates/${id}/grants`, { grants })
  },
  transfer(id: number, ownerId: number) {
    return http.post<TemplateDetail>(`/templates/${id}/transfer`, { owner_id: ownerId })
  },
  /** ONLYOFFICE 配置（mode=edit 编辑 / view 只读；未部署 DS 时 enabled=false） */
  editor(id: number, mode: 'edit' | 'view' = 'edit') {
    return http.post<EditorConfig>(`/templates/${id}/editor?mode=${mode}`)
  },
  /** 预览下载：blob 响应，silent，长超时（同步渲染） */
  preview(id: number, period: string) {
    return http.post(`/templates/${id}/preview`, { period }, {
      responseType: 'blob',
      silent: true,
      timeout: LONG_TIMEOUT_MS,
    })
  },
  /**
   * 试跑某段 AI 摘要：用当前周期真实 facts 立即出稿，不生成 docx、不落库。
   * 同步取数 + 调 AI，走长超时。
   */
  insightPreview(
    id: number,
    payload: {
      period: string
      placeholder?: string | null
      ai_config: InsightAIConfig
    },
  ) {
    return http.post<InsightPreviewResult>(`/templates/${id}/insight-preview`, payload, {
      timeout: LONG_TIMEOUT_MS,
    })
  },
}
