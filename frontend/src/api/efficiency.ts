import { http } from './http'
import type { EfficiencyValue } from './types'

export const efficiencyApi = {
  /** 首页「效率价值」：全局汇总 + 全部任务明细 + 我的任务 */
  value() {
    return http.get<EfficiencyValue>('/efficiency/value')
  },
}
