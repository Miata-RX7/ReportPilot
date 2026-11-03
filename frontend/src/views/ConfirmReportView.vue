<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'

import { confirmApi } from '@/api/reportTasks'
import type { ConfirmPreview, ConfirmResult } from '@/api/types'
import { parseApiError } from '@/utils/error'
import { formatDateTime, periodLabel } from '@/utils/format'

import { reportFileApi, type ReportFile, type SummaryDraft } from '@/api/reportFiles'

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
    summaryPlaceholders.value = data.summary_placeholders ?? []
    summaryLabels.value = data.summary_placeholder_labels ?? {}
    await loadFiles()
  } catch (error) {
    loadError.value = parseApiError(error).message
  } finally {
    loading.value = false
  }
}

/* ---------------- 上传的本期数据文件与总结 ---------------- */

const files = ref<ReportFile[]>([])
const uploading = ref(false)
const generatingId = ref<number | null>(null)
/** 每个文件的草稿：{file_id -> {prompt, text, categories, verification}} */
/**
 * 每个**占位**一个块：{占位名: {prompt, text, categories, verification}}。
 *
 * 按占位组织（而不是按文件）是因为「一个占位对应一个文件」——
 * 模板里声明了几个总结占位，确认页就该有几个输入位，用户一眼知道要交什么，
 * 也不会出现「两个文件选到同一个占位 -> 静默覆盖」。
 */
const drafts = ref<
  Record<
    string,
    {
      prompt: string
      text: string
      categories: SummaryDraft['categories']
      verification: SummaryDraft['verification']
    }
  >
>({})
/** 模板里声明的「总结_」占位（由后端随报告概要一起给出） */
const summaryPlaceholders = ref<string[]>([])
/** {占位名: 显示名}，如 { summary_assets_info: '资产信息' } */
const summaryLabels = ref<Record<string, string>>({})

/** 占位的业务名称（没配显示名时回退到占位名） */
function labelOf(ph: string): string {
  return summaryLabels.value[ph] ?? ph
}

/** 某个占位当前绑定的文件（一变量一文件） */
function fileOf(ph: string): ReportFile | undefined {
  return files.value.find((f) => f.summary?.placeholder === ph)
}

async function loadFiles() {
  try {
    const { data } = await reportFileApi.list(reportId.value, token.value)
    files.value = data
    // 回显已生成的总结（重新打开页面时不丢）—— 按占位存
    for (const f of data) {
      if (f.summary) {
        drafts.value[f.summary.placeholder] = {
          prompt: f.summary.prompt,
          text: f.summary.text,
          categories: f.summary.categories,
          verification: f.summary.verification,
        }
      }
    }
  } catch {
    // 文件列表拿不到不影响确认（可能没有需要上传的文件）
  }
}


/** el-upload 的 change 回调：模板内联表达式不支持 TS 断言，所以放脚本里 */
/** el-upload 的 change 回调：模板内联表达式不支持 TS 断言，所以放脚本里 */
function onFilePick(uploadFile: unknown, placeholder: string) {
  const f = uploadFile as { raw?: File } | undefined
  if (f?.raw) void onUpload(f.raw, placeholder)
}

async function onUpload(file: File, placeholder: string) {
  uploading.value = true
  try {
    // **上传时就带上占位** —— 文件与占位的关系由此确定
    await reportFileApi.upload(reportId.value, token.value, file, placeholder)
    ElMessage.success(`已上传到「${labelOf(placeholder)}」`)
    await loadFiles()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    uploading.value = false
  }
}


function draftOf(placeholder: string) {
  return drafts.value[placeholder]
}

function setDraft(
  placeholder: string,
  patch: Partial<{ prompt: string; text: string }>,
) {
  const cur = drafts.value[placeholder] ?? {
    prompt: '',
    text: '',
    categories: [],
    verification: { categories: 0, numbers: 0, verified: true, issues: [] },
  }
  drafts.value[placeholder] = { ...cur, ...patch }
}



async function onGenerate(placeholder: string) {
  const f = fileOf(placeholder)
  if (!f) {
    ElMessage.warning(`请先为「${labelOf(placeholder)}」上传 Excel 文件`)
    return
  }
  generatingId.value = f.id
  try {
    const d = draftOf(placeholder)
    const { data } = await reportFileApi.summarize(reportId.value, f.id, token.value, {
      prompt: d?.prompt ?? '',
    })
    drafts.value[placeholder] = {
      prompt: d?.prompt ?? '',
      text: data.text,
      categories: data.categories,
      verification: data.verification,
    }
    ElMessage.success('总结已生成，可直接编辑')
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    generatingId.value = null
  }
}



/** 溯源：某个分类对应的证据行原文（按 `${fileId}:${catIndex}` 存） */
const traceRows = ref<Record<string, { no: number; text: string }[]>>({})
/** 当前展开的气泡（同一时刻只开一个） */
const traceOpenFor = ref('')

function toggleTrace(key: string) {
  const [placeholder, ci] = key.split(':')
  const d = draftOf(placeholder)
  const cat = d?.categories[Number(ci)]
  if (!cat) return
  if (traceOpenFor.value === key) {
    traceOpenFor.value = ''
    return
  }
  traceRows.value[key] = cat.rows.map((no) => ({ no, text: `第 ${no} 行` }))
  traceOpenFor.value = key
}
async function onConfirm() {
  confirming.value = true
  try {
    // **有文件的占位必须有总结**；没上传文件的占位允许留空（提示一句即可）。
    const missing = summaryPlaceholders.value.filter((ph) => {
      const f = fileOf(ph)
      if (!f) return false
      return !draftOf(ph)?.text.trim()
    })
    if (missing.length) {
      ElMessage.warning(
        `总结为空，无法确认发送：${missing.map((ph) => labelOf(ph)).join('、')}`,
      )
      return
    }

    const summaries = summaryPlaceholders.value
      .map((ph) => {
        const f = fileOf(ph)
        const d = draftOf(ph)
        const text = d?.text.trim() ?? ''
        if (!f || !text) return null
        return {
          file_id: f.id,
          placeholder: ph,
          prompt: d?.prompt ?? '',
          text,
          categories: d?.categories ?? [],
          verification: d?.verification ?? { categories: 0, numbers: 0, verified: true, issues: [] },
        }
      })
      .filter((x) => x !== null)

    await confirmApi.confirm(reportId.value, token.value, summaries)
    // 未上传文件的占位 -> 明确告知（否则用户点了没反应，
    // 不知道那段在报告里会留占位文字）
    const noFile = summaryPlaceholders.value.filter((ph) => !fileOf(ph))
    if (noFile.length) {
      ElMessage.warning(
        `本期未上传：${noFile.map((ph) => labelOf(ph)).join('、')} —— `
        + '报告里这几段会保留占位文字',
      )
    }

    const { data } = await confirmApi.confirm(
      reportId.value,
      token.value,
      summaries,
    )
    // **必须把结果赋给 result** —— 模板靠它切换到「已确认并发送」的结果页
    result.value = data
    ElMessage.success('已确认并发送')
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
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
          <!-- 上传的本期数据文件 + AI 生成的总结（2026-11-03） -->
<div class="data-files">
  <div class="data-files__head">
    <span class="data-files__title">本期数据文件与总结</span>
    <span class="data-files__hint">
      模板里每个总结占位对应一个输入位：上传明细 Excel，系统按表格内容做分类统计并生成总结；可自行编辑后确认。
    </span>
  </div>

  <div v-for="ph in summaryPlaceholders" :key="ph" class="data-file">
    <div class="data-file__head">
      <span class="data-file__name">{{ labelOf(ph) }}</span>
      <span class="data-file__meta">{{ ph }}</span>
    </div>

    <!-- 该占位对应的文件（一变量一文件） -->
    <div class="data-file__ph">
      <template v-if="fileOf(ph)">
        <span class="data-file__name">{{ fileOf(ph)?.filename }}</span>
        <span class="data-file__meta">{{ fileOf(ph)?.row_count }} 行数据</span>
      </template>
      <template v-else>
        <el-upload
          :auto-upload="false"
          :show-file-list="false"
          accept=".xlsx,.xlsm"
          :on-change="(f: unknown) => onFilePick(f, ph)"
        >
          <el-button :loading="uploading" size="small">上传 Excel</el-button>
        </el-upload>
        <span class="data-file__meta">本期未上传（可留空）</span>
      </template>
    </div>

    <el-input
      :model-value="draftOf(ph)?.prompt ?? ''"
      placeholder="写作要求（可留空）。例如：按处理类型分类统计并列数数量"
      size="small"
      data-testid="summary-prompt"
      @update:model-value="(v: string) => setDraft(ph, { prompt: v })"
    />

    <div class="data-file__actions">
      <el-button
        type="primary"
        plain
        :loading="generatingId === fileOf(ph)?.id"
        :disabled="!fileOf(ph)"
        data-testid="summary-generate"
        @click="onGenerate(ph)"
      >
        AI 生成总结
      </el-button>
    </div>

    <el-input
      :model-value="draftOf(ph)?.text ?? ''"
      type="textarea"
      :rows="4"
      placeholder="总结文本（AI 生成后可自行编辑）"
      data-testid="summary-text"
      @update:model-value="(v: string) => setDraft(ph, { text: v })"
    />

    <!-- 溯源：数字指得出具体的行 -->
    <div v-if="draftOf(ph)?.categories?.length" class="data-file__trace">
      <div class="data-file__trace-head">
        共 {{ draftOf(ph)?.verification?.categories ?? 0 }} 个分类 ·
        {{ draftOf(ph)?.verification?.numbers ?? 0 }} 个数字 ·
        <span :class="draftOf(ph)?.verification?.verified ? 'ok' : 'warn'">
          {{ draftOf(ph)?.verification?.verified ? '全部可溯源' : '有待确认项' }}
        </span>
      </div>
      <div
        v-for="(cat, ci) in draftOf(ph)?.categories ?? []"
        :key="cat.name"
        class="trace-row"
      >
        <span class="trace-row__name">{{ cat.name }}</span>
        <span class="trace-row__count">{{ cat.count }}</span>
        <el-popover
          :visible="traceOpenFor === `${ph}:${ci}`"
          placement="top"
          trigger="click"
          popper-class="trace-bubble"
          :width="260"
          @update:visible="(v: boolean) => { if (!v) traceOpenFor = '' }"
        >
          <template #reference>
            <el-button link type="primary" @click="toggleTrace(`${ph}:${ci}`)">
              查看依据
            </el-button>
          </template>
          <div class="trace-bubble__title">{{ cat.name }}</div>
          <div class="trace-bubble__list">
            <div v-for="r in traceRows[`${ph}:${ci}`] ?? []" :key="r.no">
              {{ r.text }}
            </div>
          </div>
        </el-popover>
        <span v-if="!cat.verified" class="trace-row__warn">{{ cat.notes?.join('；') }}</span>
      </div>
    </div>
  </div>
</div>

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
  margin-top: var(--space-4, 16px);
  padding-top: var(--space-3, 12px);
  border-top: 1px solid var(--color-border-light, #e2e8f0);
  margin-top: 24px;
  text-align: center;
}

.push-error {
  color: var(--el-color-danger);
  font-size: 13px;
  margin-bottom: 8px;
}
.data-files {
  margin-top: var(--space-4);
  padding: var(--space-3);
  border: 1px solid var(--color-border-light, #e2e8f0);
  border-radius: var(--radius-md, 6px);
}
.data-files__head {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin-bottom: var(--space-3);
}
.data-files__title {
  font-weight: 600;
}
.data-files__hint,
.data-file__meta {
  color: var(--color-text-secondary, #64748b);
  font-size: var(--font-xs, 12px);
}
.data-files__upload {
  margin-bottom: var(--space-3);
}
.data-file {
  padding: var(--space-3) 0;
  border-top: 1px solid var(--color-border-lighter, #f1f5f9);
}
.data-file__head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}
.data-file__name {
  font-weight: 600;
}
.data-file__ph {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}
.data-file__ph-label {
  font-size: var(--font-xs, 12px);
  color: var(--color-text-secondary, #64748b);
  white-space: nowrap;
}
.data-file__actions {
  margin: var(--space-2) 0;
}
.data-file__trace {
  margin-top: var(--space-2);
  font-size: var(--font-xs, 12px);
}
.data-file__trace-head {
  color: var(--color-text-secondary, #64748b);
  margin-bottom: var(--space-1);
}
.data-file__trace-head .ok {
  color: var(--color-success, #047857);
}
.data-file__trace-head .warn {
  color: var(--color-warning, #c2410c);
}
.trace-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 2px 0;
}
.trace-row__count {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.trace-row__warn {
  color: var(--color-warning, #c2410c);
}
/* 溯源气泡：类似微信聊天气泡 —— 圆角矩形 + 指向性小尾巴 */
:global(.trace-bubble.el-popover) {
  padding: 8px 10px;
  border-radius: 10px;
}
:global(.trace-bubble.el-popper .el-popper__arrow::before) {
  border-radius: 10px;
}
:global(.trace-bubble .trace-bubble__title) {
  font-weight: 600;
  margin-bottom: 4px;
}
:global(.trace-bubble .trace-bubble__list) {
  max-height: 220px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-secondary, #64748b);
}

.trace-detail {
  margin: var(--space-1) 0 var(--space-2) var(--space-3);
  padding-left: var(--space-2);
  border-left: 2px solid var(--color-border-light, #e2e8f0);
  color: var(--color-text-secondary, #64748b);
}
</style>