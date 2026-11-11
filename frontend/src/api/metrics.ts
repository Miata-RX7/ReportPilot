import { http } from './http'
import type {
  MetricDefinitionCreate,
  MetricDefinitionRead,
  MetricDefinitionUpdate,
  MetricDetail,
  MetricFact,
  MetricListItem,
} from './types'

export const metricApi = {
  /** 列表：tags 传入按 AND 精确匹配（后端 jsonb 包含查询） */
  list(tags?: string[], includeIds?: number[]) {
    const params: Record<string, unknown> = {}
    if (tags?.length) params.tag = tags
    // 模板绑定页用：把「已绑定但当前用户无权编辑」的指标也拉进来，
    // 否则用户在候选列表里看不到它 -> 保存绑定时会被误删（后端也有兜底）
    if (includeIds?.length) params.include_ids = includeIds
    return http.get<MetricListItem[]>('/metrics', {
      params: Object.keys(params).length ? params : undefined,
      // 数组序列化为 tag=a&tag=b（默认会变成 tag[]=a，FastAPI 收不到）
      paramsSerializer: { indexes: null },
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
  remove(id: number) {
    return http.delete<void>(`/metrics/${id}`)
  },
  /** 试算：silent，失败由抽屉内 TestResultAlert 展示 */
  compute(id: number, period: string) {
    return http.post<MetricFact>(`/metrics/${id}/compute`, { period }, { silent: true })
  },
}
