<!--
  推送记录面板（系统设置页内的标签页）。
  从 PushRecordsView 抽出：不含页面级 PageHeader，宽度/内边距由外层
  .page-container 统一，保证与其它页面一致。
-->
<template>
  <div>
    <div class="filter-bar">
      <el-select
        v-model="filterStatus"
        placeholder="全部状态"
        class="filter-status"
        data-testid="push-filter-status"
        @change="onFilter"
      >
        <el-option label="成功" value="success" />
        <el-option label="失败" value="failed" />
      </el-select>
      <el-select
        v-model="filterAction"
        placeholder="全部动作"
        class="filter-action"
        data-testid="push-filter-action"
        @change="onFilter"
      >
        <el-option label="推送" value="push" />
        <el-option label="重试推送" value="retry_push" />
        <el-option label="确认邮件" value="confirmation" />
        <el-option label="催办提醒" value="reminder" />
      </el-select>
      <el-input
        v-model="keyword"
        placeholder="报告 id 或周期，如 12 / 2026-09"
        class="filter-keyword"
        clearable
        data-testid="push-filter-keyword"
        @keyup.enter="onFilter"
        @clear="onFilter"
      />
      <el-button type="primary" data-testid="push-filter-submit" @click="onFilter">查询</el-button>
      <el-button data-testid="push-filter-reset" @click="onReset">重置</el-button>
      <el-button :loading="loading" data-testid="push-records-refresh" @click="load">刷新</el-button>
      <span class="text-muted total-hint">共 {{ total }} 条</span>
    </div>

    <el-alert
      v-if="failedCount > 0"
      type="warning"
      show-icon
      :closable="false"
      class="failed-hint"
      data-testid="push-failed-hint"
      :title="`有 ${failedCount} 条推送失败记录，可在报告管理里对对应报告点「重试推送」`"
    />

    <el-table v-loading="loading" :data="rows" empty-text="暂无推送记录">
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="报告" min-width="200">
        <template #default="{ row }">
          <div>{{ row.report_name }}</div>
          <div class="text-muted report-id">#{{ row.report_id }}</div>
        </template>
      </el-table-column>
      <el-table-column label="结果" width="90">
        <template #default="{ row }">
          <span
            :class="[
              'soft-tag',
              row.status === 'success' ? 'soft-tag--success' : 'soft-tag--danger',
            ]"
            :data-testid="`push-status-${row.status}`"
          >
            {{ STATUS_LABEL[row.status as PushRecordStatus] }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="动作" width="100">
        <template #default="{ row }">{{ ACTION_LABEL[row.action as PushRecordAction] }}</template>
      </el-table-column>
      <el-table-column label="第几次" width="80">
        <template #default="{ row }">#{{ row.attempt }}</template>
      </el-table-column>
      <el-table-column label="收件人" min-width="240">
        <template #default="{ row }">
          <span v-if="row.recipients.length">
            <el-tag
              v-for="(r, i) in row.recipients"
              :key="`${row.id}-${i}`"
              size="small"
              class="recipient-tag"
            >
              {{ r }}
            </el-tag>
          </span>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="附件" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="code-chip">{{ row.attachment_name ?? '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="耗时" width="90">
        <template #default="{ row }">{{ duration(row.duration_ms) }}</template>
      </el-table-column>
      <el-table-column label="操作人" width="90">
        <template #default="{ row }">
          <span v-if="row.actor_id">#{{ row.actor_id }}</span>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="失败原因" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.error" class="error-text">{{ row.error }}</span>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  pushRecordApi,
  type PushRecord,
  type PushRecordAction,
  type PushRecordStatus,
} from '@/api/pushRecords'
import { parseApiError } from '@/utils/error'
import { formatDateTime } from '@/utils/format'

const rows = ref<PushRecord[]>([])
const total = ref(0)
const loading = ref(false)

const filterStatus = ref<'' | PushRecordStatus>('')
const filterAction = ref<'' | PushRecordAction>('')
const keyword = ref('')

const STATUS_LABEL: Record<PushRecordStatus, string> = {
  success: '成功',
  failed: '失败',
}
const ACTION_LABEL: Record<PushRecordAction, string> = {
  push: '推送',
  retry_push: '重试推送',
  confirmation: '确认邮件',
  reminder: '催办提醒',
}

/** 当前页内的失败条数（提示用；服务端分页前的近似值） */
const failedCount = computed(() => rows.value.filter((r) => r.status === 'failed').length)

async function load() {
  loading.value = true
  try {
    const { data } = await pushRecordApi.list({
      status: filterStatus.value || null,
      action: filterAction.value || null,
      keyword: keyword.value.trim() || null,
      limit: 200,
    })
    rows.value = data.items
    total.value = data.total
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    loading.value = false
  }
}

function onFilter() {
  void load()
}

function onReset() {
  filterStatus.value = ''
  filterAction.value = ''
  keyword.value = ''
  void load()
}

/** 耗时展示：ms -> 可读文本 */
function duration(ms: number | null): string {
  if (ms === null) return '—'
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

onMounted(load)
</script>

<style scoped lang="scss">
.filter-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.filter-status {
  width: 130px;
}

.filter-action {
  width: 140px;
}

.filter-keyword {
  width: 240px;
}

.total-hint {
  font-size: 13px;
}

.failed-hint {
  margin-bottom: var(--space-3);
}

.report-id {
  font-size: 12px;
}

.recipient-tag {
  margin: 1px 2px;
}

.error-text {
  color: var(--danger-color, #ef4444);
}
</style>