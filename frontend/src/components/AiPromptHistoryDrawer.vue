/**
 * 系统提示词版本历史抽屉（append-only 账本）。
 *
 * 每次保存 / 恢复默认 / 回滚都会追加一条记录；回滚不改写历史，只是再写一条新的。
 * 列表不含全文（点开才拉详情），避免响应被撑大。
 */
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { systemApi, type AiPromptVersionDetail, type AiPromptVersionSummary } from '@/api/system'
import { confirmDanger } from '@/components/ConfirmDialog'
import { parseApiError } from '@/utils/error'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const loading = ref(false)
const rolling = ref<number | null>(null)
const versions = ref<AiPromptVersionSummary[]>([])
const detail = ref<AiPromptVersionDetail | null>(null)
const detailLoading = ref(false)

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const ACTION_META: Record<string, { text: string; type: 'primary' | 'warning' | 'info' }> = {
  save: { text: '保存', type: 'primary' },
  reset_default: { text: '恢复默认', type: 'warning' },
  rollback: { text: '回滚', type: 'info' },
}

function actionMeta(action: string) {
  return ACTION_META[action] ?? { text: action, type: 'info' as const }
}

function fmt(iso: string): string {
  return iso.replace('T', ' ').slice(0, 19)
}

/** 当前生效的那条 = 列表里最新的一条（保存 / 恢复默认 / 回滚都会写一条新的） */
const currentId = computed(() => versions.value[0]?.id ?? null)

async function load() {
  loading.value = true
  try {
    versions.value = await systemApi.aiPromptVersions(50)
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    loading.value = false
  }
}

async function openDetail(row: AiPromptVersionSummary) {
  if (detail.value?.id === row.id) {
    detail.value = null
    return
  }
  detailLoading.value = true
  try {
    detail.value = await systemApi.aiPromptVersion(row.id)
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    detailLoading.value = false
  }
}

async function rollback(row: AiPromptVersionSummary) {
  const ok = await confirmDanger(
    '回滚系统提示词',
    '将把两段规则恢复为版本 #' +
      row.id +
      '（' +
      fmt(row.created_at) +
      '）的内容，并追加一条回滚记录（历史不会被改写）。',
  )
  if (!ok) return
  rolling.value = row.id
  try {
    await systemApi.rollbackAiPrompt(row.id)
    ElMessage.success('已回滚到该版本')
    detail.value = null
    await load()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    rolling.value = null
  }
}

watch(visible, (v) => {
  if (v) {
    detail.value = null
    void load()
  }
})
</script>

<template>
  <el-drawer
    v-model="visible"
    title="系统提示词 · 版本历史"
    size="520px"
    data-testid="ai-prompt-history"
  >
    <p class="drawer-hint">
      每次保存 / 恢复默认 / 回滚都会留一条记录；回滚<strong>不改写</strong>历史，只是再写一条新的。
    </p>

    <el-skeleton v-if="loading" :rows="4" animated />
    <el-empty v-else-if="!versions.length" description="还没有任何修改记录" />

    <el-timeline v-else class="version-timeline">
      <el-timeline-item
        v-for="row in versions"
        :key="row.id"
        :type="actionMeta(row.action).type"
        :hollow="row.id !== currentId"
      >
        <div class="version-head">
          <el-tag :type="actionMeta(row.action).type" size="small" effect="light">
            {{ actionMeta(row.action).text }}
          </el-tag>
          <span class="version-id">#{{ row.id }}</span>
          <el-tag v-if="row.id === currentId" type="success" size="small" effect="plain">
            当前
          </el-tag>
        </div>
        <div class="version-meta">
          <span>{{ row.actor_name || '—' }}</span>
          <span class="dot">·</span>
          <span>{{ fmt(row.created_at) }}</span>
        </div>
        <div class="version-len">
          基础规则 {{ row.base_len }} 字 · 追加规则 {{ row.rules_len }} 字
        </div>
        <div class="version-preview">{{ row.base_preview || '（空：使用内置默认）' }}</div>
        <div class="version-actions">
          <el-button link type="primary" size="small" @click="openDetail(row)">
            {{ detail?.id === row.id ? '收起内容' : '查看内容' }}
          </el-button>
          <el-button
            v-if="row.id !== currentId"
            link
            type="warning"
            size="small"
            :loading="rolling === row.id"
            :data-testid="'ai-prompt-rollback-' + row.id"
            @click="rollback(row)"
          >
            回滚到此版本
          </el-button>
        </div>

        <div v-if="detail?.id === row.id" v-loading="detailLoading" class="version-detail">
          <div class="detail-block">
            <div class="detail-label">基础规则（{{ row.base_len }} 字）</div>
            <pre class="detail-text">{{ detail.base_prompt || '（空：使用内置默认）' }}</pre>
          </div>
          <div class="detail-block">
            <div class="detail-label">追加规则（{{ row.rules_len }} 字）</div>
            <pre class="detail-text">{{ detail.system_rules || '（空：使用内置默认）' }}</pre>
          </div>
        </div>
      </el-timeline-item>
    </el-timeline>
  </el-drawer>
</template>

<style scoped lang="scss">
.drawer-hint {
  margin: 0 0 var(--space-4);
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.6;
}

.version-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.version-id {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.version-meta {
  margin-top: 2px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.dot {
  margin: 0 4px;
}

.version-len {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.version-preview {
  margin-top: 4px;
  padding: 6px 8px;
  border-radius: 4px;
  background: var(--color-bg);
  color: var(--color-text-secondary);
  font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-all;
}

.version-actions {
  margin-top: 4px;
  display: flex;
  gap: var(--space-2);
}

.version-detail {
  margin-top: 6px;
}

.detail-block + .detail-block {
  margin-top: var(--space-3);
}

.detail-label {
  color: var(--color-text-secondary);
  font-size: 12px;
  margin-bottom: 2px;
}

.detail-text {
  margin: 0;
  max-height: 220px;
  overflow: auto;
  padding: 8px;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-bg);
  font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>