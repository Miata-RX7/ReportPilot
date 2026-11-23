import { http } from './http'
import type {
  MetricDefinitionCreate,
  MetricDefinitionRead,
  MetricDefinitionUpdate,
  MetricDetail,
  MetricFact,
  MetricListItem,
  MetricPage,
} from './types'

export const metricApi = {
  /**
   * 分页列出指标（列表页用）。全量 list() 留给模板绑定页。
   *
   * `tag` 多选为**并集**语义（任一标签命中即显示），由后端 `_tag_filter_or` 实现；
   * 数组参数的序列化由 http 层的全局 `paramsSerializer` 统一处理。
   */
  paged(params: {
    page?: number
    page_size?: number
    q?: string
    status?: 'active' | 'inactive' | 'all'
    tag?: string[]
    /** 归属范围：all=全部 / mine=仅我的 / shared=角色组共享的（后端未实现时忽略该参数） */
    scope?: 'all' | 'mine' | 'shared'
  }) {
    const q: Record<string, unknown> = {}
    if (params.page) q.page = params.page
    if (params.page_size) q.page_size = params.page_size
    if (params.q) q.q = params.q
    if (params.status) q.status = params.status
    if (params.tag?.length) q.tag = params.tag
    if (params.scope) q.scope = params.scope
    return http.get<MetricPage>('/metrics/paged', { params: q })
  },

  list(tags?: string[], includeIds?: number[]) {
    const params: Record<string, unknown> = {}
    if (tags?.length) params.tag = tags
    // 模板绑定页用：把「已绑定但当前用户无权编辑」的指标也拉进来，
    // 否则用户在候选列表里看不到它 -> 保存绑定时会被误删（后端也有兜底）
    if (includeIds?.length) params.include_ids = includeIds
    return http.get<MetricListItem[]>('/metrics', {
      params: Object.keys(params).length ? params : undefined,
      // 数组参数（tag / include_ids）由 **http 层的全局 paramsSerializer**
      // 统一序列化成重复 key（tag=a&tag=b），这里不要再单独配。
    })
  },
  /** 详情：附带 query_config_resolved（id -> name） */
  detail(id: number) {
    return http.get<MetricDetail>(`/metrics/${id}`)
  },
  create(payload: MetricDefinitionCreate) {
    return http.post<MetricDefinitionRead>('/metrics', payload)
  },
  update(id: number, payload: MetricDefinitionUpdate) {
    return http.put<MetricDefinitionRead>(`/metrics/${id}`, payload)
  },
  /** 该指标被哪些模板的哪些变量绑定（删除前提示用） */
  templateBindings(id: number) {
    return http.get<{
      template_id: number
      template_name: string
      placeholder: string
      version_id: number
    }[]>(`/metrics/${id}/template-bindings`)
  },
  /** 哪些计算型指标引用了这个指标（删除前展示关系） */
  referencedBy(id: number) {
    return http.get<{
      calc_id: number
      calc_name: string
      var_name: string
    }[]>(`/metrics/${id}/referenced-by`)
  },
  remove(id: number) {
    return http.delete<void>(`/metrics/${id}`)
  },
  /** 试算：silent，失败由抽屉内 TestResultAlert 展示 */
  compute(id: number, period: string) {
    return http.post<MetricFact>(`/metrics/${id}/compute`, { period }, { silent: true })
  },
}
