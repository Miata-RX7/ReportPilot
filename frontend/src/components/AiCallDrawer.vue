<script setup lang="ts">
/**
 * 「查看 AI 请求」抽屉：把一次请求 AI 的完整参数、prompt 与响应摊开。
 *
 * 数据来自 ai_call_logs —— 后端在 `ai_insight.generate_summary`（全系统 AI 的
 * 唯一出口）埋点，报告生成 / 单段重生成 / 模板试跑三个入口都有留痕。
 *
 * 隐私：表中不含 api_key，base_url 已去 userinfo；本组件只读。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { aiCallApi, type AiCallQuery } from '@/api/aiCalls'
import type { AiCallDetail, AiCallItem } from '@/api/types'
import { parseApiError } from '@/utils/error'
import { formatDateTime } from '@/utils/format'

const props = defineProps<{
  modelValue: boolean
  /** 抽屉标题；调用方一般会带上报告名或槽位名 */
  title?: string
  /** 过滤条件：按报告（可再按槽位收窄）或按模板 */
  query: AiCallQuery
}>()

const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const items = ref<AiCallItem[]>([])
const total = ref(0)
const loading = ref(false)
const detail = ref<AiCallDetail | null>(null)
const detailLoading = ref(false)
const activeId = ref<number | null>(null)

const KIND_LABEL: Record<string, string> = {
  generate: '报告生成',
  regenerate: '单段重生成',
  preview: '模板试跑',
}

const STATUS_LABEL: Record<string, string> = {
  success: '成功',
  degraded: '降级',
  failed: '失败',
}

function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind
}

function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status
}

/** 降级 = AI 不可用留下的占位文本，用黄色区分于正常出稿 */
function statusTagType(status: string): 'success' | 'warning' | 'danger' {
  if (status === 'success') return 'success'
  if (status === 'degraded') return 'warning'
  return 'danger'
}

function durationText(ms: number | null): string {
  if (ms === null || ms === undefined) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`
}

/** 重试请求的最后一条 user 消息 = 纠正指令，单独提出来与首稿对比 */
const retryCorrection = computed(() => {
  const msgs = detail.value?.retry_messages ?? []
  const last = msgs[msgs.length - 1]
  return last?.role === 'user' ? last.content : ''
})

async function load() {
  loading.value = true
  try {
    const { data } = await aiCallApi.list({ ...props.query, limit: 50 })
    items.value = data.items
    total.value = data.total
    if (data.items.length) {
      await open(data.items[0].id)
    } else {
      detail.value = null
      activeId.value = null
    }
  } catch (e) {
    ElMessage.error(parseApiError(e) || '加载 AI 请求记录失败')
  } finally {
    loading.value = false
  }
}

async function open(id: number) {
  activeId.value = id
  detailLoading.value = true
  try {
    const { data } = await aiCallApi.detail(id)
    detail.value = data
  } catch (e) {
    ElMessage.error(parseApiError(e) || '加载 AI 请求详情失败')
  } finally {
    detailLoading.value = false
  }
}

/** 一键复制完整 JSON：排查问题时可整段贴给他人 */
async function copyJson() {
  if (!detail.value) return
  const text = JSON.stringify(detail.value, null, 2)
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制本次请求的完整 JSON')
  } catch {
    ElMessage.warning('浏览器不允许访问剪贴板，请手动选中文本复制')
  }
}

watch(visible, (v) => {
  if (v) void load()
})
</script>

<template>
  <el-drawer
    v-model="visible"
    :title="title || 'AI 请求详情'"
    size="70%"
    data-testid="ai-call-drawer"
  >
    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="ai-call-tip"
      title="这里记录的是真正发给 AI 的内容：完整 messages（system 规则 + user 段的写作要求 / 范文 / 指标 JSON）、模型参数、响应原文与护栏重试。不含 API Key；每个报告或模板只保留最近 50 条。"
    />

    <div v-loading="loading" class="ai-call-body">
      <p v-if="!items.length && !loading" class="text-muted ai-call-empty">
        暂无 AI 请求记录 —— 本次升级之后新发生的 AI 调用才会留痕。
      </p>

      <template v-else>
        <div class="ai-call-list-title text-muted">
          请求历史：共 {{ total }} 条，显示最近 {{ items.length }} 条（点击行查看详情）
        </div>
        <el-table
          :data="items"
          row-key="id"
          :current-row-key="activeId ?? undefined"
          size="small"
          highlight-current-row
          max-height="220"
          @row-click="(row: AiCallItem) => open(row.id)"
        >
          <el-table-column label="时间" width="160">
            <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="场景" width="96">
            <template #default="{ row }">{{ kindLabel(row.kind) }}</template>
          </el-table-column>
          <el-table-column label="槽位" min-width="110" show-overflow-tooltip>
            <template #default="{ row }">{{ row.slot || '—' }}</template>
          </el-table-column>
          <el-table-column label="周期" width="90">
            <template #default="{ row }">{{ row.period || '—' }}</template>
          </el-table-column>
          <el-table-column label="模型" min-width="120" show-overflow-tooltip>
            <template #default="{ row }">{{ row.model || '—' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="120">
            <template #default="{ row }">
              <el-tag :type="statusTagType(row.status)" size="small">
                {{ statusLabel(row.status) }}
              </el-tag>
              <el-tag v-if="row.retried" type="warning" size="small" class="ai-call-retried">
                重试
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="82">
            <template #default="{ row }">{{ durationText(row.duration_ms) }}</template>
          </el-table-column>
        </el-table>

        <div v-if="detail" v-loading="detailLoading" class="ai-call-detail">
          <dl class="ai-call-params">
            <div>
              <dt>场景</dt>
              <dd>{{ kindLabel(detail.kind) }}</dd>
            </div>
            <div>
              <dt>槽位</dt>
              <dd>{{ detail.slot || '—' }}</dd>
            </div>
            <div>
              <dt>统计周期</dt>
              <dd>{{ detail.period || '—' }}</dd>
            </div>
            <div>
              <dt>模型</dt>
              <dd>{{ detail.model || '—' }}</dd>
            </div>
            <div>
              <dt>temperature</dt>
              <dd>{{ detail.temperature ?? '—' }}</dd>
            </div>
            <div>
              <dt>max_tokens</dt>
              <dd>{{ detail.max_tokens ?? '—' }}</dd>
            </div>
            <div>
              <dt>超时</dt>
              <dd>{{ detail.timeout_seconds ?? '—' }} s</dd>
            </div>
            <div>
              <dt>耗时</dt>
              <dd>{{ durationText(detail.duration_ms) }}</dd>
            </div>
            <div>
              <dt>请求时间</dt>
              <dd>{{ formatDateTime(detail.created_at) }}</dd>
            </div>
            <div class="ai-call-param-wide">
              <dt>base_url</dt>
              <dd>{{ detail.base_url || '—' }}</dd>
            </div>
          </dl>

          <div v-if="detail.error" class="ai-call-block">
            <div class="ai-call-block-title">失败原因</div>
            <pre class="ai-call-pre ai-call-pre--error">{{ detail.error }}</pre>
          </div>

          <div class="ai-call-block">
            <div class="ai-call-block-title">
              请求 messages（{{ detail.messages.length }} 条）
              <span class="text-muted">system = 固定红线规则；user = 写作要求 + 范文 + 指标数据</span>
            </div>
            <div v-for="(msg, i) in detail.messages" :key="`msg-${i}`" class="ai-call-msg">
              <span class="ai-call-role" :class="`ai-call-role--${msg.role}`">{{ msg.role }}</span>
              <pre class="ai-call-pre">{{ msg.content }}</pre>
            </div>
            <p v-if="!detail.messages.length" class="text-muted">
              本次未构造请求（例如未配置 API Key 就直接降级）。
            </p>
          </div>

          <div class="ai-call-block">
            <div class="ai-call-block-title">AI 响应原文</div>
            <pre v-if="detail.response_text" class="ai-call-pre">{{ detail.response_text }}</pre>
            <p v-else class="text-muted">（无响应：请求未成功返回）</p>
          </div>

          <div v-if="detail.retried" class="ai-call-block">
            <div class="ai-call-block-title">
              护栏重试（命中范文示例数字 / 条目数超出范文）
              <span class="text-muted">重试版仅在违规更少时被采纳，最终文本见报告正文</span>
            </div>
            <div class="ai-call-block-sub">纠正指令</div>
            <pre class="ai-call-pre">{{ retryCorrection || '—' }}</pre>
            <div class="ai-call-block-sub">重试响应原文</div>
            <pre v-if="detail.retry_response_text" class="ai-call-pre">{{ detail.retry_response_text }}</pre>
            <p v-else class="text-muted">
              （重试请求未返回文本{{ detail.retry_error ? `：${detail.retry_error}` : '' }}）
            </p>
          </div>
        </div>
      </template>
    </div>

    <template #footer>
      <el-button :disabled="!detail" data-testid="ai-call-copy" @click="copyJson">
        复制完整 JSON
      </el-button>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-drawer>
</template>

<style scoped>
.ai-call-tip {
  margin-bottom: 12px;
}

.ai-call-body {
  min-height: 160px;
}

.ai-call-empty {
  padding: 24px 0;
  text-align: center;
}

.ai-call-list-title {
  margin-bottom: 6px;
  font-size: 12px;
}

.ai-call-retried {
  margin-left: 4px;
}

.ai-call-detail {
  margin-top: 16px;
}

.ai-call-params {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px 16px;
  margin: 0 0 8px;
}

.ai-call-params dt {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.ai-call-params dd {
  margin: 2px 0 0;
  font-size: 13px;
  word-break: break-all;
}

.ai-call-param-wide {
  grid-column: 1 / -1;
}

.ai-call-block {
  margin-top: 16px;
}

.ai-call-block-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 13px;
  font-weight: 600;
}

.ai-call-block-sub {
  margin: 8px 0 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.ai-call-msg {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 8px;
}

.ai-call-role {
  flex: none;
  min-width: 66px;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--el-fill-color);
  color: var(--el-text-color-regular);
  font-size: 12px;
  text-align: center;
}

.ai-call-role--system {
  background: var(--el-color-info-light-8);
}

.ai-call-role--user {
  background: var(--el-color-primary-light-8);
}

.ai-call-role--assistant {
  background: var(--el-color-success-light-8);
}

.ai-call-pre {
  flex: 1;
  max-height: 320px;
  margin: 0;
  padding: 8px 10px;
  overflow: auto;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  background: var(--el-fill-color-lighter);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.ai-call-pre--error {
  color: var(--el-color-danger);
}
</style>