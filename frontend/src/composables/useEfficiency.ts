/**
 * 首页统一请求「效率价值」数据，**只请求一次**，再分发给两个卡片。
 *
 * 之前两个卡片各自调 efficiencyApi.value() -> 首页会发两次相同请求
 * （playwright 实测：/api/efficiency/value × 2）。2026-11-20。
 *
 * 注意：不能只用「loaded 布尔」去重 —— 两个组件的 onMounted 几乎同时触发，
 * 第一个请求还在 await 时 loaded 还是 false -> 第二个也会发出去。
 * 所以用**共享的 in-flight promise**。
 */
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { efficiencyApi } from '@/api/efficiency'
import type { EfficiencyValue } from '@/api/types'
import { parseApiError } from '@/utils/error'

const data = ref<EfficiencyValue | null>(null)
const loading = ref(false)
const failed = ref(false)

let loaded = false
let inflight: Promise<void> | null = null

export function useEfficiency() {
  async function doLoad(): Promise<void> {
    loading.value = true
    failed.value = false
    try {
      const { data: d } = await efficiencyApi.value()
      data.value = d
      loaded = true
    } catch (error) {
      failed.value = true
      ElMessage.error(parseApiError(error).message)
    } finally {
      loading.value = false
    }
  }

  function load(force = false): Promise<void> {
    if (inflight) return inflight
    if (loaded && !force) return Promise.resolve()
    inflight = doLoad().finally(() => {
      inflight = null
    })
    return inflight
  }

  onMounted(() => {
    void load()
  })

  return { data, loading, failed, load }
}
