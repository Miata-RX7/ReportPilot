<script setup lang="ts">
/**
 * 首页「我的任务效率」卡片（2026-11-20）。
 *
 * 全局卡片给的是「全部任务」的视角；这一张给**当前用户名下**的任务 ——
 * 让每个用户看到自己那部分贡献。
 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { efficiencyApi } from '@/api/efficiency'
import type { EfficiencySummary, TaskEfficiencyRead } from '@/api/types'
import { parseApiError } from '@/utils/error'

const summary = ref<EfficiencySummary | null>(null)
const tasks = ref<TaskEfficiencyRead[]>([])
const loading = ref(false)
const failed = ref(false)

async function load() {
  loading.value = true
  failed.value = false
  try {
    const { data } = await efficiencyApi.value()
    summary.value = data.mine_summary
    tasks.value = data.mine
  } catch (error) {
    failed.value = true
    ElMessage.error(parseApiError(error).message)
  } finally {
    loading.value = false
  }
}
load()

function humanMinutes(min: number): string {
  if (!min) return '0 分钟'
  if (min < 60) return `${min} 分钟`
  const hours = min / 60
  if (hours < 24) return `${hours.toFixed(1)} 小时`
  return `${(hours / 24).toFixed(1)} 天`
}
</script>

<template>
  <el-card shadow="never" class="my-efficiency-card" v-loading="loading">
    <template #header>
      <div class="my-efficiency-head">
        <span class="my-efficiency-title">我的任务效率</span>
        <span class="text-muted">我创建的任务累计省下的人工时间</span>
      </div>
    </template>

    <el-alert
      v-if="failed"
      type="error"
      show-icon
      :closable="false"
      title="加载失败"
      class="mb12"
    />

    <template v-else>
      <div v-if="tasks.length" class="my-efficiency-body">
        <div class="my-efficiency-kpi">
          <div class="my-efficiency-kpi__value">
            {{ humanMinutes(summary?.total_saved_minutes ?? 0) }}
          </div>
          <div class="my-efficiency-kpi__label">累计节省</div>
        </div>
        <el-table :data="tasks" size="small" class="my-efficiency-table">
          <el-table-column label="任务" min-width="160">
            <template #default="{ row }">{{ row.task_name }}</template>
          </el-table-column>
          <el-table-column label="执行" width="70" align="right">
            <template #default="{ row }">{{ row.run_count }} 次</template>
          </el-table-column>
          <el-table-column label="每次节省" width="100" align="right">
            <template #default="{ row }">{{ humanMinutes(row.saved_per_run) }}</template>
          </el-table-column>
          <el-table-column label="累计节省" width="110" align="right">
            <template #default="{ row }">
              <span :class="row.total_saved > 0 ? 'eff-positive' : 'text-muted'">
                {{ humanMinutes(row.total_saved) }}
              </span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <el-empty v-else description="你还没有创建报告任务" :image-size="60" />
    </template>
  </el-card>
</template>

<style scoped lang="scss">
.my-efficiency-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.my-efficiency-title {
  font-weight: 600;
}
.my-efficiency-body {
  display: flex;
  gap: 24px;
  align-items: flex-start;
  flex-wrap: wrap;
}
.my-efficiency-kpi {
  min-width: 130px;
}
.my-efficiency-kpi__value {
  font-size: 20px;
  font-weight: 600;
  line-height: 1.3;
}
.my-efficiency-kpi__label {
  font-size: 12px;
  color: var(--rp-text-muted, #909399);
}
.my-efficiency-table {
  flex: 1;
  min-width: 380px;
}
.eff-positive {
  color: #67c23a;
  font-weight: 600;
}
.mb12 {
  margin-bottom: 12px;
}
</style>
