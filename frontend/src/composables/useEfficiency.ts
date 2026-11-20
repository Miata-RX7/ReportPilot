/**
 * 首页统一请求「效率价值」数据，**只请求一次**，再分发给两个卡片。
 *
 * 之前两个卡片各自调 efficiencyApi.value() -> 首页会发两次相同请求
 * （playwright 实测：/api/efficiency/value × 2）。2026-11-20。
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

export function useEfficiency() {
  async function load(force = false) {
    if (loaded && !force) return
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

  onMounted(() => {
    void load()
  })

  return { data, loading, failed, load }
}
