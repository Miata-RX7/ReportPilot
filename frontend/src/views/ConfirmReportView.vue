<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'

import { confirmApi } from '@/api/reportTasks'
import type { ConfirmPreview, ConfirmResult } from '@/api/types'
import { parseApiError } from '@/utils/error'
import { formatDateTime, periodLabel } from '@/utils/format'

const route = useRoute()

const preview = ref<ConfirmPreview | null>(null)
const result = ref<ConfirmResult | null>(null)
const loading = ref(true)
const confirming = ref(false)
const loadError = ref<string | null>(null)

const reportId = computed(() => Number(route.params.id))
const token = computed(() => String(route.query.token ?? ''))

/** token 状态 -> 页面主文案与配色 */
const stateText = computed(() => {
  const st = preview.value?.token_state
  if (result.value) return null
  switch (st) {
    case 'valid':
      return null
    case 'expired':
      return { title: '确认链接已过期', desc: preview.value?.message ?? '请联系管理员重新发送确认邮件' }
    case 'used':
      return { title: '该链接已使用', desc: preview.value?.message ?? '如需重新推送请联系管理员' }
    default:
      return { title: '确认链接无效', desc: preview.value?.message ?? '请检查链接是否完整复制' }
  }
})

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const { data } = await confirmApi.preview(reportId.value, token.value)
    preview.value = data
  } catch (error) {
    loadError.value = parseApiError(error).message
  } finally {
    loading.value = false
  }
}

async function onConfirm() {
  confirming.value = true
  try {
    const { data } = await confirmApi.confirm(reportId.value, token.value)
    result.value = data
    ElMessage.success(data.message)
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
    // token 可能已被别人用掉，刷新状态
    await load()
  } finally {
    confirming.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="confirm-page">
    <div class="confirm-card">
      <div class="confirm-brand">ReportPilot</div>

      <div v-if="loading" class="confirm-loading">正在加载…</div>

      <el-alert
        v-else-if="loadError"
        type="error"
        show-icon
        :closable="false"
        :title="loadError"
      />

      <template v-else-if="result">
        <el-result
          :icon="result.push_status === 'pushed' ? 'success' : 'warning'"
          :title="result.push_status === 'pushed' ? '已确认并发送' : '已确认，但发送失败'"
          :sub-title="result.message"
        >
          <template #extra>
            <p v-if="result.push_error" class="push-error">{{ result.push_error }}</p>
            <p class="text-muted">本页面可关闭；发送结果已记录在系统的推送记录中。</p>
          </template>
        </el-result>
      </template>

      <template v-else-if="stateText">
        <el-result
          icon="warning"
          :title="stateText.title"
          :sub-title="stateText.desc"
        />
      </template>

      <template v-else-if="preview">
        <h1 class="confirm-title">报告确认</h1>
        <p class="text-muted confirm-sub">
          请确认以下报告内容无误后点击按钮，报告将立即发送给收件人。
        </p>

        <el-descriptions :column="1" border class="confirm-desc">
          <el-descriptions-item label="报告">{{ preview.report_name }}</el-descriptions-item>
          <el-descriptions-item label="统计周期">{{ periodLabel(preview.period) }}</el-descriptions-item>
          <el-descriptions-item label="生成时间">
            {{ formatDateTime(preview.created_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="收件人">
            <el-tag v-for="r in preview.recipient_emails" :key="r" size="small" class="rec-tag">
              {{ r }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="指标概况">
            共 {{ preview.metric_count }} 个指标
            <span v-if="preview.abnormal_count > 0" class="abnormal">
              ，{{ preview.abnormal_count }} 个异常（变化超过 10%）
            </span>
          </el-descriptions-item>
          <el-descriptions-item label="确认有效期">
            {{ formatDateTime(preview.expires_at) }}
            <el-tag v-if="preview.is_expired" type="warning" size="small" class="ml4">已过期</el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <div v-if="preview.ai_summary" class="ai-summary">
          <div class="ai-summary__title">AI 摘要</div>
          <div class="ai-summary__body">{{ preview.ai_summary }}</div>
          <div class="text-muted ai-summary__hint">
            摘要中的数字由 AI 生成，可能未经核对；完整数据请在系统报告详情中查看。
          </div>
        </div>

        <el-alert
          type="info"
          show-icon
          :closable="false"
          class="privacy-hint"
          title="为避免运营数据外泄，此页不展示具体指标数值"
        />

        <div class="confirm-actions">
          <el-button
            type="primary"
            size="large"
            :loading="confirming"
            data-testid="confirm-submit"
            @click="onConfirm"
          >
            确认并发送给收件人
          </el-button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped lang="scss">
.confirm-page {
  min-height: 100vh;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 48px 16px;
  background: var(--color-bg, #f5f7fa);
}

.confirm-card {
  width: 100%;
  max-width: 720px;
  background: #fff;
  border-radius: 8px;
  padding: 32px;
  box-shadow: 0 2px 12px rgb(0 0 0 / 8%);
}

.confirm-brand {
  font-size: 18px;
  font-weight: 600;
  margin-bottom: 24px;
}

.confirm-title {
  font-size: 22px;
  font-weight: 600;
  margin: 0 0 4px;
}

.confirm-sub {
  margin: 0 0 20px;
  font-size: 14px;
}

.confirm-loading {
  text-align: center;
  padding: 40px 0;
  color: #909399;
}

.rec-tag {
  margin: 2px 4px 2px 0;
}

.abnormal {
  color: var(--el-color-warning);
  font-weight: 600;
}

.ml4 {
  margin-left: 4px;
}

.ai-summary {
  margin-top: 20px;
  padding: 16px;
  background: #f7f9fc;
  border-radius: 6px;
}

.ai-summary__title {
  font-weight: 600;
  margin-bottom: 8px;
}

.ai-summary__body {
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.ai-summary__hint {
  font-size: 12px;
  margin-top: 8px;
}

.privacy-hint {
  margin-top: 16px;
}

.confirm-actions {
  margin-top: 24px;
  text-align: center;
}

.push-error {
  color: var(--el-color-danger);
  font-size: 13px;
  margin-bottom: 8px;
}
</style>