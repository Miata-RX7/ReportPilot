import { http } from './http'
import type {
  ConnectionTestResult,
  DataSourceCreate,
  DataSourceRead,
  DataSourceUpdate,
  HostGroupRef,
  HostRef,
  ItemRef,
  ItemRowRef,
  PieSuitability,
  PieSuitabilityRequest,
} from './types'

export const dataSourceApi = {
  list() {
    return http.get<DataSourceRead[]>('/datasources')
  },
  create(payload: DataSourceCreate) {
    return http.post<DataSourceRead>('/datasources', payload)
  },
  update(id: number, payload: DataSourceUpdate) {
    return http.put<DataSourceRead>(`/datasources/${id}`, payload)
  },
  remove(id: number) {
    return http.delete<void>(`/datasources/${id}`)
  },
  /** 连通性测试：silent，失败提示由行内 TestResultAlert 展示，避免连续测试刷屏 */
  test(id: number) {
    return http.post<ConnectionTestResult>(`/datasources/${id}/test`, {}, { silent: true })
  },
  /** 主机组下拉（级联第一级） */
  listHostGroups(id: number) {
    return http.get<HostGroupRef[]>(`/datasources/${id}/host-groups`)
  },
  /** 主机 / 实例下拉（级联第二级）：group_ids 必传；q 为 AIOps 实例搜索关键词 */
  listHosts(id: number, groupIds: string[], q?: string) {
    return http.get<HostRef[]>(`/datasources/${id}/hosts`, {
      params: { group_ids: groupIds.join(','), ...(q ? { q } : {}) },
    })
  },
  /** pie 适用性判定：7 天窗口 + 5 秒超时（后端），超时返回 suitable='unknown' */
  analyzePieSuitability(id: number, payload: PieSuitabilityRequest) {
    return http.post<PieSuitability>(`/datasources/${id}/analyze-pie-suitability`, payload, {
      timeout: 30000,
    })
  },
  /** 监控项下拉（级联第三级）：host_ids 必传，q 为名称/键子串过滤 */
  listItems(id: number, hostIds: string[], q?: string) {
    return http.get<ItemRef[]>(`/datasources/${id}/items`, {
      params: { host_ids: hostIds.join(','), ...(q ? { q } : {}) },
    })
  },
  /**
   * AIOps 级联第四级：list 型监控项的主键行。
   *
   * 必须传 `columnId` —— 后端按「该列当前值为空」过滤掉无数据的行
   * （实测「当前值非空」⟺「有历史」），所以**换了列要重新拉**，列表会变。
   * 仅 AIOps 可用，其他类型返回 422。
   */
  listItemRows(id: number, instanceId: string, itemId: string, columnId: string) {
    return http.get<ItemRowRef[]>(`/datasources/${id}/item-rows`, {
      params: { instance_id: instanceId, item_id: itemId, column_id: columnId },
    })
  },
}
