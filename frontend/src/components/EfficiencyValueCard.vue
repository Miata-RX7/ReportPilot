<script setup lang="ts">
/**
 * 首页「效率价值」卡片（2026-11-20）。
 *
 * 回答「这套系统到底省了多少时间」：按任务逐行，对比「人工写一份报告要多久」
 * 与「平台上配置一次 + 让它自动跑」。
 *
 * 口径（与用户确认）：
 * - 人工：数值指标 1 分钟/个、曲线图 2 分钟/个、AI 摘要 5 分钟/占位符、
 *   确认阶段表格统计 15 分钟（仅当确实上传过 Excel 并生成了摘要才计入）
 * - 平台：配置数值 2、曲线图 4、AI 摘要 2（**一次性**）；确认阶段 3 分钟/次
 * - 执行次数只算成功终态（pushed / confirmed_no_push）
 */
import { computed, ref } from 'vue'
import { useEfficiency } from '@/composables/useEfficiency'

// 数据由 useEfficiency 统一取（首页只发一次请求）—— 两个卡片共用同一份
const { data, loading, failed } = useEfficiency()

const summary = computed(() => data.value?.summary ?? null)
const tasks = computed(() => data.value?.tasks ?? [])
/** 口径说明默认折叠 */
const showBasis = ref(false)

/** 分钟 -> 人性化（小时/天） */
function humanMinutes(min: number): string {
  if (!min) return '0 分钟'
  if (min < 60) return `${min} 分钟`
  const hours = min / 60
  if (hours < 24) return `${hours.toFixed(1)} 小时`
  return `${(hours / 24).toFixed(1)} 天`
}

const savedText = computed(() => humanMinutes(summary.value?.total_saved_minutes ?? 0))
const manualText = computed(() =>
  humanMinutes(summary.value?.total_manual_minutes ?? 0),
)
const platformText = computed(() =>
  humanMinutes(summary.value?.total_platform_minutes ?? 0),
)
</script>

<template>
  <el-card shadow="never" class="efficiency-card" v-loading="loading">
    <template #header>
      <div class="efficiency-head">
        <span class="efficiency-title">效率价值</span>
        <span class="text-muted">人工写这些报告要多久，平台替我们省了多少</span>
        <el-button
          link
          type="primary"
          class="efficiency-basis-btn"
          @click="showBasis = !showBasis"
        >
          {{ showBasis ? '收起口径说明' : '口径说明' }}
        </el-button>
      </div>
    </template>

    <el-alert
      v-if="failed"
      type="error"
      show-icon
      :closable="false"
      title="效率数据加载失败"
      class="mb12"
    />

    <template v-else>
      <!-- 汇总 -->
      <div class="efficiency-summary">
        <div class="efficiency-kpi">
          <div class="efficiency-kpi__value">{{ savedText }}</div>
          <div class="efficiency-kpi__label">累计节省人工时间</div>
        </div>
        <div class="efficiency-kpis">
          <div class="efficiency-kpi">
            <div class="efficiency-kpi__value">{{ summary?.run_count ?? 0 }} 次</div>
            <div class="efficiency-kpi__label">自动执行</div>
          </div>
          <div class="efficiency-kpi">
            <div class="efficiency-kpi__value">{{ summary?.task_count ?? 0 }} 个</div>
            <div class="efficiency-kpi__label">报告任务</div>
          </div>
          <div class="efficiency-kpi efficiency-kpi--muted">
            <div class="efficiency-kpi__value">{{ manualText }}</div>
            <div class="efficiency-kpi__label">人工原本要花</div>
          </div>
          <div class="efficiency-kpi efficiency-kpi--muted">
            <div class="efficiency-kpi__value">{{ platformText }}</div>
            <div class="efficiency-kpi__label">平台实际投入</div>
          </div>
        </div>
      </div>

      <!-- 口径说明 -->
      <el-alert
        v-if="showBasis"
        type="info"
        :closable="false"
        class="mb12 efficiency-basis"
      >
        <template #title>计算口径</template>
        <ul class="efficiency-basis__list">
          <li>人工：数值指标 1 分钟/个、曲线图 2 分钟/个、AI 摘要 5 分钟/个、确认阶段表格统计 15 分钟</li>
          <li>平台：配置数值指标 2 分钟/个、曲线图 4 分钟/个、AI 摘要 2 分钟/个（<b>只配置一次</b>）</li>
          <li>确认阶段（仅当该报告确实上传过 Excel 并执行了 AI 生成摘要才计入）：人工 15 分钟/次、平台 3 分钟/次（上传 1 + 生成 2）</li>
          <li>执行次数只统计成功的报告（已推送 / 已确认未发送）</li>
          <li>累计节省 = 执行次数 × 人工每次 − 平台一次性配置 − 执行次数 × 平台每次</li>
        </ul>
      </el-alert>

      <!-- 每个任务一行 -->
      <el-table
        v-if="tasks.length"
        :data="tasks"
        empty-text="还没有报告任务"
        class="efficiency-table"
      >
        <el-table-column label="任务" min-width="180">
          <template #default="{ row }">
            <div>{{ row.task_name }}</div>
            <div class="text-muted efficiency-sub">
              {{ row.template_name ?? '—' }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="构成" min-width="220">
          <template #default="{ row }">
            <el-tag v-if="row.scalar_count" size="small" effect="plain" class="mr4">
              数值 × {{ row.scalar_count }}
            </el-tag>
            <el-tag v-if="row.curve_count" size="small" effect="plain" class="mr4">
              曲线 × {{ row.curve_count }}
            </el-tag>
            <el-tag v-if="row.insight_count" size="small" effect="plain" class="mr4">
              AI 摘要 × {{ row.insight_count }}
            </el-tag>
            <el-tag v-if="row.has_excel_summary" size="small" type="warning" effect="plain">
              含 Excel 统计
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="执行" width="80" align="right">
          <template #default="{ row }">{{ row.run_count }} 次</template>
        </el-table-column>
        <el-table-column label="人工每次" width="110" align="right">
          <template #default="{ row }">{{ humanMinutes(row.manual_per_run) }}</template>
        </el-table-column>
        <el-table-column label="平台一次性" width="110" align="right">
          <template #default="{ row }">{{ humanMinutes(row.platform_setup) }}</template>
        </el-table-column>
        <el-table-column label="每次节省" width="110" align="right">
          <template #default="{ row }">
            <span :class="row.saved_per_run > 0 ? 'eff-positive' : 'text-muted'">
              {{ humanMinutes(row.saved_per_run) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="累计节省" width="120" align="right">
          <template #default="{ row }">
            <span :class="row.total_saved > 0 ? 'eff-positive' : 'text-muted'">
              {{ humanMinutes(row.total_saved) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="回本" width="90" align="right">
          <template #default="{ row }">
            <span v-if="row.breakeven_runs !== null" class="text-muted">
              {{ row.breakeven_runs }} 次
            </span>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-else description="还没有报告任务，先去创建一个" :image-size="70" />
    </template>
  </el-card>
</template>

<style scoped lang="scss">
.efficiency-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.efficiency-title {
  font-weight: 600;
}
.efficiency-basis-btn {
  margin-left: auto;
}
.efficiency-summary {
  display: flex;
  gap: 24px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.efficiency-kpi__value {
  font-size: 22px;
  font-weight: 600;
  line-height: 1.3;
}
.efficiency-kpi--muted .efficiency-kpi__value {
  font-size: 16px;
  font-weight: 500;
  color: var(--rp-text-muted, #909399);
}
.efficiency-kpi__label {
  font-size: 12px;
  color: var(--rp-text-muted, #909399);
}
.efficiency-kpis {
  display: flex;
  gap: 28px;
  flex-wrap: wrap;
}
.efficiency-basis__list {
  margin: 0;
  padding-left: 18px;
  line-height: 1.8;
}
.efficiency-sub {
  font-size: 12px;
}
.eff-positive {
  color: #67c23a;
  font-weight: 600;
}
.mr4 {
  margin-right: 4px;
}
.mb12 {
  margin-bottom: 12px;
}
</style>
