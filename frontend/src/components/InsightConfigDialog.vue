<script setup lang="ts">
/**
 * 「AI 摘要」槽位配置弹窗：写作要求（提示词）+ 参考范文 + 试跑预览。
 *
 * 试跑是提示词好不好用的关键反馈回路：用当前周期真实 facts 立即出稿，
 * 不生成整篇报告、不落库；确认无误后再点「确定」回填到绑定表并随模板保存。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { templateApi } from '@/api/templates'
import type { InsightAIConfig, InsightPreviewResult } from '@/api/types'
import AiCallDrawer from '@/components/AiCallDrawer.vue'
import PeriodPicker from '@/components/PeriodPicker.vue'
import { parseApiError } from '@/utils/error'
import { previousPeriod } from '@/utils/format'

const props = defineProps<{
  modelValue: boolean
  templateId: number
  /** 该段对应的 Word 占位符名（后端会用它作为槽位 key） */
  placeholder: string
  config: InsightAIConfig | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  save: [config: InsightAIConfig]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const form = ref<InsightAIConfig>({})
/** 默认试跑周期：上个月（月报惯例）；切到周粒度由 PeriodPicker 换成上周 */
const period = ref(previousPeriod())
const running = ref(false)
const result = ref<InsightPreviewResult | null>(null)
/** 试跑同样留痕：抽屉里能看到这次试跑真正发出去的 messages 与响应 */
const aiCallVisible = ref(false)

/**
 * 每次打开都用 props.config 重置表单。
 *
 * `immediate` 不能省：父组件用 `v-if="aiCfgRow"` 挂载本组件，而 `openAiConfig()` 在同一个 tick 里
 * 同时设了 `aiCfgRow` 与 `aiCfgVisible`，所以本组件是**带着 modelValue 已经为 true 被创建的**，
 * `modelValue` 不会再"变化"——没有 immediate 时这个 watch 一次都不会触发，表单停在空对象，
 * 表现为「硬刷新后第一次点开写作要求/参考范文都是空的，第二次才正常」。
 *
 * 同理要监听 config 本身：父组件可能先打开弹窗、之后才拿到/回填 aiConfig。
 */
watch(
  [() => props.modelValue, () => props.config],
  ([open]) => {
    if (!open) return
    form.value = { ...(props.config ?? {}) }
    result.value = null
  },
  { immediate: true, deep: true },
)

/** 清除本段覆盖：两段置空 = 沿用全局默认（不是清空规则） */
function clearSystemOverride() {
  form.value.system_prompt = null
  form.value.system_rules = null
}

async function runPreview() {
  if (!period.value) {
    ElMessage.warning('请先选择试跑周期')
    return
  }
  running.value = true
  try {
    const { data } = await templateApi.insightPreview(props.templateId, {
      period: period.value,
      placeholder: props.placeholder,
      ai_config: form.value,
    })
    result.value = data
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    running.value = false
  }
}

function confirm() {
  emit('save', { ...form.value })
  visible.value = false
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="AI 摘要配置"
    width="min(760px, 94vw)"
    top="6vh"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <div class="ai-cfg-head">
      <span class="code-chip">{{ placeholder }}</span>
      <span class="text-muted">本段独立生成，配置仅作用于这个变量</span>
    </div>

    <el-form label-position="top">
      <el-form-item label="写作要求（提示词）">
        <el-input
          v-model="form.prompt"
          type="textarea"
          :rows="3"
          maxlength="4000"
          show-word-limit
          placeholder="例如：只总结与 Gi0/0/21 相关的指标；用一句话点出风险与影响范围。"
          data-testid="insight-cfg-prompt"
        />
      </el-form-item>

      <el-form-item>
        <template #label>
          <span>
            参考范文（可选）
            <el-tooltip
              content="粘贴一段以往真实写过的内容，它就是本段输出的模板：AI 照它的句式与条目数写，只把数值与单位换成本次真实数据；范文里的示例数字不会被沿用"
              placement="top"
            >
              <span class="hint-dot">?</span>
            </el-tooltip>
          </span>
        </template>
        <el-input
          v-model="form.sample_text"
          type="textarea"
          :rows="5"
          maxlength="4000"
          show-word-limit
          placeholder="粘贴一段以往真实写过的段落，例如：本期核心链路带宽利用率整体平稳，峰值出现在……"
          data-testid="insight-cfg-sample"
        />
      </el-form-item>

      <div class="ai-cfg-grid">
        <el-form-item label="字数上限">
          <el-input-number
            :model-value="form.max_chars ?? undefined"
            :min="50"
            :max="2000"
            :step="50"
            placeholder="默认 200"
            data-testid="insight-cfg-max-chars"
            @update:model-value="(v: number | undefined) => (form.max_chars = v ?? null)"
          />
          <span class="text-muted form-hint">
            留空 = 200 字；若填了超过 200 字的范文，则按范文长度为准（不截范文）。想更长请直接填数字。
          </span>
        </el-form-item>
        <el-form-item label="温度">
          <el-input-number
            :model-value="form.temperature ?? undefined"
            :min="0"
            :max="2"
            :step="0.1"
            :precision="1"
            placeholder="默认 0.2"
            @update:model-value="(v: number | undefined) => (form.temperature = v ?? null)"
          />
        </el-form-item>
        <el-form-item label="显著波动阈值(%)">
          <el-input-number
            :model-value="form.threshold_pct ?? undefined"
            :min="0.1"
            :max="100"
            :step="0.5"
            :precision="1"
            placeholder="默认全局"
            @update:model-value="(v: number | undefined) => (form.threshold_pct = v ?? null)"
          />
        </el-form-item>
      </div>

      <!-- 高级：绑定级系统提示词覆盖（默认收起，避免主流程变复杂） -->
      <el-collapse class="ai-cfg-advanced">
        <el-collapse-item name="system">
          <template #title>
            <span class="ai-cfg-advanced-title">
              本段系统提示词（高级，留空 = 用全局默认）
              <el-tag v-if="form.system_prompt || form.system_rules" type="primary" size="small" effect="plain">
                本段已覆盖
              </el-tag>
            </span>
          </template>
          <p class="text-muted ai-cfg-advanced-hint">
            覆盖「系统设置」里的全局两段规则，只对这个槽位生效。留空即沿用全局；两段都留空时，
            本段用全局，全局也没配则用内置默认。
          </p>
          <el-form-item label="基础规则（覆盖全局规则 1-5）">
            <el-input
              v-model="form.system_prompt"
              type="textarea"
              :rows="5"
              maxlength="4000"
              show-word-limit
              placeholder="留空 = 用全局默认"
              data-testid="insight-cfg-system-prompt"
            />
          </el-form-item>
          <el-form-item label="追加规则（覆盖全局规则 6-8）">
            <el-input
              v-model="form.system_rules"
              type="textarea"
              :rows="5"
              maxlength="4000"
              show-word-limit
              placeholder="留空 = 用全局默认"
              data-testid="insight-cfg-system-rules"
            />
          </el-form-item>
          <el-button
            v-if="form.system_prompt || form.system_rules"
            link
            type="primary"
            data-testid="insight-cfg-clear-system"
            @click="clearSystemOverride"
          >
            清除本段覆盖（恢复为全局默认）
          </el-button>
        </el-collapse-item>
      </el-collapse>
    </el-form>

    <el-divider content-position="left">试跑预览</el-divider>

    <div class="ai-preview-bar">
      <PeriodPicker v-model="period" testid="insight-preview-period" />
      <el-button
        type="primary"
        plain
        :loading="running"
        data-testid="insight-preview-run"
        @click="runPreview"
      >
        试跑
      </el-button>
      <span v-if="result" class="text-muted">共 {{ result.metric_count }} 个指标参与</span>
    </div>

    <div v-if="result" class="ai-preview-result" data-testid="insight-preview-result">
      <el-alert
        v-if="result.degraded"
        type="warning"
        show-icon
        :closable="false"
        title="AI 不可用，已降级为占位文本（请检查「系统设置」中的 AI 配置）"
        class="ai-alert"
      />
      <el-alert
        v-if="result.sample_leak?.length"
        type="error"
        show-icon
        :closable="false"
        class="ai-alert"
        :title="`输出照抄了参考范文中的数字：${result.sample_leak.join('、')}，请调整范文或提示词`"
      />
      <el-alert
        v-if="result.unverified_numbers.length"
        type="warning"
        show-icon
        :closable="false"
        class="ai-alert"
        :title="`以下数字未在事实数据中溯源，请人工核对：${result.unverified_numbers.join('、')}`"
      />
      <p class="ai-preview-text">{{ result.text }}</p>
      <div class="ai-preview-actions">
        <el-tag v-if="result.retried" type="warning" size="small">已自动纠正重试</el-tag>
        <el-button
          size="small"
          text
          type="info"
          data-testid="insight-ai-call"
          @click="aiCallVisible = true"
        >
          查看本次 AI 请求
        </el-button>
      </div>
    </div>
    <p v-else class="text-muted">
      点击「试跑」可用该周期的真实数据立即看到效果，不会生成整篇报告、也不会保存配置。
    </p>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" data-testid="insight-cfg-save" @click="confirm">
        确定
      </el-button>
    </template>

    <!-- 试跑留痕：这次点「试跑」真正发给 AI 的 messages 与响应原文 -->
    <AiCallDrawer
      v-model="aiCallVisible"
      :title="`AI 请求 · 试跑 ${placeholder}`"
      :query="{ templateId, slot: placeholder, kind: 'preview', limit: 50 }"
    />
  </el-dialog>
</template>

<style scoped>
.ai-cfg-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.ai-cfg-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.ai-cfg-advanced {
  margin-top: var(--space-2);
}

.ai-cfg-advanced-title {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 13px;
}

.ai-cfg-advanced-hint {
  margin: 0 0 var(--space-3);
  font-size: 12px;
  line-height: 1.6;
}

.ai-preview-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.ai-preview-result {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 12px;
  background: var(--el-fill-color-lighter);
}

.ai-alert {
  margin-bottom: 8px;
}

.ai-preview-text {
  margin: 8px 0;
  line-height: 1.7;
  white-space: pre-wrap;
}

.ai-preview-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* 字数上限旁的说明：grid 单元格内换行显示，别把 input-number 挤歪 */
.form-hint {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
}

.hint-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  font-size: 10px;
  color: #fff;
  background: var(--el-color-info);
  cursor: help;
}
</style>