/**
 * 全局系统提示词编辑器（两段规则文本）。
 *
 * 设计要点：
 * - 两段**独立**：基础规则（规则 1-5，所有摘要必带）/ 追加规则（6-8，仅当该段配了
 *   写作要求或范文时才追加）。不能合并，否则 6-8 会泄漏到所有摘要。
 * - 留空 = 回落内置默认（内置文本由后端实时下发，不在前端硬编码）。
 * - 文本里可写 {threshold_pct} / {max_chars} 两个动态变量，其余花括号原样保留。
 * - 编辑器不做红线校验（用户已知风险），但改动会立即影响后续所有 AI 摘要，故保存前提示。
 */
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { systemApi, type AiPromptPreviewResult, type AiPromptRead } from '@/api/system'
import { templateApi } from '@/api/templates'
import type { TemplateDetail, TemplateListItem } from '@/api/types'
import AiCallDrawer from '@/components/AiCallDrawer.vue'
import PeriodPicker from '@/components/PeriodPicker.vue'
import { confirmDanger } from '@/components/ConfirmDialog'
import { parseApiError } from '@/utils/error'
import { previousPeriod } from '@/utils/format'

const MAX_LEN = 4000

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  /** 保存 / 恢复默认成功后通知父级刷新卡片状态标签 */
  (e: 'saved'): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const loading = ref(false)
const saving = ref(false)
const data = ref<AiPromptRead | null>(null)
const basePrompt = ref('')
const systemRules = ref('')

/** 内置默认预览 */
const showDefault = ref(false)

/** 试跑 */
const templates = ref<TemplateListItem[]>([])
const templateId = ref<number | null>(null)
const detail = ref<TemplateDetail | null>(null)
const slot = ref<string>('')
const period = ref(previousPeriod())
const running = ref(false)
const result = ref<AiPromptPreviewResult | null>(null)
const aiCallVisible = ref(false)

const slots = computed(() =>
  (detail.value?.bindings ?? []).filter((b) => b.binding_type === 'insight'),
)

const dirty = computed(() => {
  if (!data.value) return false
  return (
    basePrompt.value !== (data.value.base_prompt ?? '') ||
    systemRules.value !== (data.value.system_rules ?? '')
  )
})

async function load() {
  loading.value = true
  try {
    data.value = await systemApi.aiPrompt()
    basePrompt.value = data.value.base_prompt ?? ''
    systemRules.value = data.value.system_rules ?? ''
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    loading.value = false
  }
}

async function loadSlots() {
  detail.value = null
  slot.value = ''
  result.value = null
  if (!templateId.value) return
  try {
    detail.value = (await templateApi.detail(templateId.value)).data
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}
async function save() {
  if (dirty.value) {
    const ok = await confirmDanger(
      '保存系统提示词',
      '保存后将立即影响后续所有 AI 摘要（报告生成、单段重生成、试跑），无需重启服务。确定保存？',
    )
    if (!ok) return
  }
  saving.value = true
  try {
    data.value = await systemApi.saveAiPrompt({
      base_prompt: basePrompt.value,
      system_rules: systemRules.value,
    })
    basePrompt.value = data.value.base_prompt ?? ''
    systemRules.value = data.value.system_rules ?? ''
    ElMessage.success('系统提示词已保存，立即生效')
    emit('saved')
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    saving.value = false
  }
}

async function resetDefault() {
  const ok = await confirmDanger(
    '恢复内置默认',
    '将清空两段自定义文本，回到内置默认规则（历史会留一条记录，可随时回滚）。确定恢复？',
  )
  if (!ok) return
  saving.value = true
  try {
    data.value = await systemApi.resetAiPrompt()
    basePrompt.value = ''
    systemRules.value = ''
    ElMessage.success('已恢复内置默认')
    emit('saved')
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    saving.value = false
  }
}

async function runPreview() {
  if (!templateId.value) {
    ElMessage.warning('请先选择模板')
    return
  }
  if (!slot.value) {
    ElMessage.warning('请先选择 AI 槽位')
    return
  }
  if (!period.value) {
    ElMessage.warning('请先选择试跑周期')
    return
  }
  running.value = true
  result.value = null
  try {
    result.value = await systemApi.previewAiPrompt({
      template_id: templateId.value,
      placeholder: slot.value,
      period: period.value,
      base_prompt: basePrompt.value,
      system_rules: systemRules.value,
    })
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    running.value = false
  }
}

watch(visible, async (v) => {
  if (!v) return
  result.value = null
  showDefault.value = false
  await load()
  if (!templates.value.length) {
    try {
      templates.value = (await templateApi.list()).data
    } catch (error) {
      ElMessage.error(parseApiError(error).message)
    }
  }
})
</script>
<template>
  <el-dialog
    v-model="visible"
    title="系统提示词（AI 固定规则）"
    width="760px"
    top="6vh"
    data-testid="ai-prompt-dialog"
  >
    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="prompt-hint"
      title="这里改的是 AI 的固定写作规则，只影响文字组织方式，不参与任何指标计算，也不会改变报告里的数值口径。"
    />

    <el-skeleton v-if="loading" :rows="6" animated />

    <template v-else>
      <section class="prompt-block">
        <div class="block-head">
          <span class="block-title">基础规则（所有摘要必带）</span>
          <el-tooltip
            content="对应内置规则的第 1-5 条：只许引用指标数据里的数值、禁止推算换算、字数上限、波动阈值口径。规则 1 直接关系到「AI 不算数」这条底线，删改前请三思。"
            placement="top"
          >
            <span class="help-dot">?</span>
          </el-tooltip>
        </div>
        <el-input
          v-model="basePrompt"
          type="textarea"
          :rows="8"
          resize="vertical"
          :maxlength="MAX_LEN"
          show-word-limit
          class="prompt-textarea"
          placeholder="留空 = 使用内置默认"
          data-testid="ai-prompt-base"
        />
        <p class="block-note">
          可用变量：<code>{threshold_pct}</code>（波动阈值）、
          <code>{max_chars}</code>（字数上限）；其它花括号（如 JSON 片段）会原样保留。
        </p>
      </section>

      <section class="prompt-block">
        <div class="block-head">
          <span class="block-title">追加规则（仅当该段配了写作要求或参考范文时才附加）</span>
          <el-tooltip
            content="对应内置规则的第 6-8 条：写作要求不得越过红线、只输出正文、参考范文即输出模板。这段不会泄漏到没配范文 / 写作要求的摘要。"
            placement="top"
          >
            <span class="help-dot">?</span>
          </el-tooltip>
        </div>
        <el-input
          v-model="systemRules"
          type="textarea"
          :rows="8"
          resize="vertical"
          :maxlength="MAX_LEN"
          show-word-limit
          class="prompt-textarea"
          placeholder="留空 = 使用内置默认"
          data-testid="ai-prompt-rules"
        />
      </section>

      <div class="prompt-toolbar">
        <el-button
          link
          type="primary"
          data-testid="ai-prompt-show-default"
          @click="showDefault = !showDefault"
        >
          {{ showDefault ? '收起内置默认' : '查看内置默认' }}
        </el-button>
        <span class="toolbar-spacer" />
        <el-tag v-if="dirty" type="warning" size="small" effect="plain">未保存</el-tag>
        <el-tag v-else-if="data && !data.has_custom" type="info" size="small" effect="plain">
          内置默认
        </el-tag>
        <el-tag v-else type="primary" size="small" effect="plain">已自定义</el-tag>
        <el-button :loading="saving" data-testid="ai-prompt-reset" @click="resetDefault">
          恢复默认
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          data-testid="ai-prompt-save"
          @click="save"
        >
          保存
        </el-button>
      </div>

      <div v-if="showDefault && data" class="default-preview">
        <div class="detail-label">内置基础规则</div>
        <pre class="default-text">{{ data.default_base_prompt }}</pre>
        <div class="detail-label">内置追加规则</div>
        <pre class="default-text">{{ data.default_system_rules }}</pre>
      </div>
      <el-divider content-position="left">改完直接试跑（用页面上的文本，不会自动保存）</el-divider>

      <div class="preview-bar">
        <el-select
          v-model="templateId"
          placeholder="选择模板"
          filterable
          class="preview-select"
          data-testid="ai-prompt-preview-template"
          @change="loadSlots"
        >
          <el-option v-for="t in templates" :key="t.id" :label="t.name" :value="t.id" />
        </el-select>
        <el-select
          v-model="slot"
          placeholder="AI 槽位"
          :disabled="!detail"
          class="preview-select"
          data-testid="ai-prompt-preview-slot"
        >
          <el-option
            v-for="b in slots"
            :key="b.id"
            :label="b.placeholder"
            :value="b.placeholder"
          />
        </el-select>
        <PeriodPicker v-model="period" testid="ai-prompt-preview-period" />
        <el-button
          type="primary"
          plain
          :loading="running"
          data-testid="ai-prompt-preview-run"
          @click="runPreview"
        >
          试跑
        </el-button>
      </div>
      <p v-if="detail && !slots.length" class="text-muted">
        该模板还没有 AI 摘要绑定，请先在模板里绑定一个 AI 占位符。
      </p>

      <div v-if="result" class="preview-result" data-testid="ai-prompt-preview-result">
        <el-alert
          v-if="result.degraded"
          type="warning"
          show-icon
          :closable="false"
          title="AI 不可用，已降级为占位文本（请检查「系统设置」中的 AI 配置）"
        />
        <el-alert
          v-if="result.sample_leak.length"
          type="error"
          show-icon
          :closable="false"
          :title="'输出照抄了参考范文里的示例数字：' + result.sample_leak.join('、')"
        />
        <el-alert
          v-if="result.unverified_numbers.length"
          type="warning"
          show-icon
          :closable="false"
          :title="'有数字未溯源（可能来自规则之外的数据），需人工核对：' + result.unverified_numbers.join('、')"
        />
        <div class="result-body">
          <p class="result-meta">
            {{ result.model }} · 共 {{ result.metric_count }} 个指标参与
            <el-tag v-if="result.retried" type="warning" size="small">
              命中范文护栏后已自动纠正重试
            </el-tag>
          </p>
          <div class="result-text">{{ result.text }}</div>
          <el-button link type="primary" size="small" @click="aiCallVisible = true">
            查看本次 AI 请求
          </el-button>
        </div>
      </div>
    </template>

    <AiCallDrawer
      v-model="aiCallVisible"
      title="AI 请求 · 系统提示词试跑"
      :query="{ templateId: templateId ?? 0, slot, kind: 'preview', limit: 50 }"
    />
  </el-dialog>
</template>
<style scoped lang="scss">
.prompt-hint {
  margin-bottom: var(--space-4);
}

.prompt-block + .prompt-block {
  margin-top: var(--space-4);
}

.block-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: 6px;
}

.block-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--color-text-primary);
}

.help-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-size: 12px;
  cursor: help;
}

.prompt-textarea :deep(.el-textarea__inner) {
  font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 12px;
  line-height: 1.7;
}

.block-note {
  margin: 6px 0 0;
  color: var(--color-text-tertiary);
  font-size: 12px;

  code {
    padding: 0 4px;
    border-radius: 3px;
    background: var(--color-neutral-bg);
    font-size: 11px;
  }
}

.prompt-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

.toolbar-spacer {
  flex: 1;
}

.default-preview {
  margin-top: var(--space-3);
  padding: 10px;
  border: 1px dashed var(--color-border-strong);
  border-radius: 4px;
  background: var(--color-bg);
}

.detail-label {
  color: var(--color-text-secondary);
  font-size: 12px;
  margin: 4px 0 2px;
}

.default-text {
  margin: 0;
  max-height: 200px;
  overflow: auto;
  padding: 8px;
  border-radius: 4px;
  background: var(--color-bg-elevated);
  font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.preview-bar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.preview-select {
  width: 200px;
}

.preview-result {
  margin-top: var(--space-3);
}

.result-body {
  margin-top: var(--space-2);
  padding: 10px;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-bg-elevated);
}

.result-meta {
  margin: 0 0 6px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.result-text {
  white-space: pre-wrap;
  line-height: 1.8;
  font-size: 13px;
}
</style>