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
  list(tags?: string[]) {
    return http.get<MetricListItem[]>('/metrics', {
      params: tags?.length ? { tag: tags } : undefined,
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
