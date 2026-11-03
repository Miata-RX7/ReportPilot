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
const drafts = ref<
  Record<
    number,
    {
      placeholder: string
      prompt: string
      text: string
      categories: SummaryDraft['categories']
      verification: SummaryDraft['verification']
    }
  >
>({})
/** 模板里声明的「总结_」占位（由后端随报告概要一起给出），供用户挑选 */
const summaryPlaceholders = ref<string[]>([])

async function loadFiles() {
  try {
    const { data } = await reportFileApi.list(reportId.value, token.value)
    files.value = data
    // 回显已生成的总结（重新打开页面时不丢）
    for (const f of data) {
      if (f.summary) {
        drafts.value[f.id] = {
          placeholder: f.summary.placeholder,
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
function onFilePick(uploadFile: unknown) {
  const f = uploadFile as { raw?: File } | undefined
  if (f?.raw) void onUpload(f.raw)
}

async function onUpload(file: File) {
  uploading.value = true
  try {
    await reportFileApi.upload(reportId.value, token.value, file)
    ElMessage.success('文件已上传')
    await loadFiles()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    uploading.value = false
  }
}

/**
 * 默认占位名。
 *
 * **优先用模板里声明的第一个总结占位**（用户不用选就能对上）；
 * 模板里没有总结占位时才退回 summary_{fileId}。
 *
 * 注意不能用中文前缀：占位变量名只允许 ASCII 字母数字下划线
 * （docx_parser.VAR_RE，且 Jinja 也要求如此），中文名会被判为非法语法，
 * 导致 ONLYOFFICE 保存被拒、编辑内容丢失（2026-11-03 线上事故）。
 */
function defaultPlaceholder(fileId: number): string {
  // 已被别的文件占用的占位不能再默认选 —— 否则两个文件都落到同一个占位，
  // 后者会**静默覆盖**前者（save_summary 按 report_id+placeholder 唯一）。
  const taken = new Set(
    Object.entries(drafts.value)
      .filter(([fid]) => Number(fid) !== fileId)
      .map(([, d]) => d.placeholder)
      .filter(Boolean),
  )
  const free = summaryPlaceholders.value.find((ph) => !taken.has(ph))
  return free ?? `summary_${fileId}`
}

function draftOf(fileId: number) {
  return drafts.value[fileId]
}

function setDraft(
  fileId: number,
  patch: Partial<{ placeholder: string; prompt: string; text: string }>,
) {
  const cur = drafts.value[fileId] ?? {
    placeholder: defaultPlaceholder(fileId),
    prompt: '',
    text: '',
    categories: [],
    verification: { categories: 0, numbers: 0, verified: true, issues: [] },
  }
  drafts.value[fileId] = { ...cur, ...patch }
}

async function onGenerate(file: ReportFile) {
  generatingId.value = file.id
  try {
    const d = draftOf(file.id)
    const { data } = await reportFileApi.summarize(reportId.value, file.id, token.value, {
      prompt: d?.prompt ?? '',
    })
    drafts.value[file.id] = {
      placeholder: d?.placeholder ?? defaultPlaceholder(file.id),
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

/** 溯源：某个分类对应的证据行原文 */
const traceRows = ref<Record<number, { no: number; text: string }[]>>({})
function showTrace(file: ReportFile, catIndex: number) {
  const d = draftOf(file.id)
  const cat = d?.categories[catIndex]
  if (!cat) return
  traceRows.value[file.id] = cat.rows.map((no) => ({
    no,
    text: `第 ${no} 行属于「${cat.name}」`,
  }))
}

async function onConfirm() {
  confirming.value = true
  try {
    const used = new Map<string, number>()
    for (const [fileId, d] of Object.entries(drafts.value)) {
      if (!d.text.trim()) continue
      const prev = used.get(d.placeholder)
      if (prev !== undefined) {
        ElMessage.warning(
          `两个文件都填到了占位「${d.placeholder}」，请改成不同的占位`,
        )
        return
      }
      used.set(d.placeholder, Number(fileId))
    }
    const summaries = Object.entries(drafts.value)
      .filter(([, d]) => d.text.trim())
      .map(([fileId, d]) => ({
        file_id: Number(fileId),
        placeholder: d.placeholder || defaultPlaceholder(Number(fileId)),
        prompt: d.prompt,
        text: d.text,
        categories: d.categories,
        verification: d.verification,
      }))
    const { data } = await confirmApi.confirm(reportId.value, token.value, summaries)
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
          <!-- 上传的本期数据文件 + AI 生成的总结（2026-11-03） -->
      <div class="data-files">
        <div class="data-files__head">
          <span class="data-files__title">本期数据文件与总结</span>
          <span class="data-files__hint">
            上传明细 Excel，系统会按表格内容做分类统计并生成总结；可自行编辑后确认。
          </span>
        </div>

        <div class="data-files__upload">
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            accept=".xlsx,.xlsm"
            :on-change="onFilePick"
          >
            <el-button :loading="uploading">上传 Excel 文件</el-button>
          </el-upload>
        </div>

        <div v-for="f in files" :key="f.id" class="data-file">
          <div class="data-file__head">
            <span class="data-file__name">{{ f.filename }}</span>
            <span class="data-file__meta">{{ f.row_count }} 行数据</span>
          </div>

          <!-- 占位选择：这个总结填到模板的哪一处 -->
          <div class="data-file__ph">
            <span class="data-file__ph-label">填入占位</span>
            <el-select
              :model-value="draftOf(f.id)?.placeholder ?? ''"
              placeholder="选择模板中的总结占位"
              size="small"
              data-testid="summary-placeholder"
              @update:model-value="(v: string) => setDraft(f.id, { placeholder: v })"
            >
              <el-option
                v-for="ph in summaryPlaceholders"
                :key="ph"
                :label="ph"
                :value="ph"
              />
              <el-option
                v-if="!summaryPlaceholders.includes(draftOf(f.id)?.placeholder ?? '')"
                :label="draftOf(f.id)?.placeholder ?? ''"
                :value="draftOf(f.id)?.placeholder ?? ''"
              />
            </el-select>
          </div>

          <el-input
            :model-value="draftOf(f.id)?.prompt ?? ''"
            placeholder="写作要求（可留空）。例如：按处理类型分类统计并列数数量"
            data-testid="summary-prompt"
            @update:model-value="(v: string) => setDraft(f.id, { prompt: v })"
          />

          <div class="data-file__actions">
            <el-button
              type="primary"
              plain
              :loading="generatingId === f.id"
              data-testid="summary-generate"
              @click="onGenerate(f)"
            >
              AI 生成总结
            </el-button>
          </div>

          <el-input
            :model-value="draftOf(f.id)?.text ?? ''"
            type="textarea"
            :rows="4"
            placeholder="总结文本（AI 生成后可自行编辑）"
            data-testid="summary-text"
            @update:model-value="(v: string) => setDraft(f.id, { text: v })"
          />

          <!-- 溯源：数字指得出具体的行 -->
          <div v-if="draftOf(f.id)?.categories?.length" class="data-file__trace">
            <div class="data-file__trace-head">
              共 {{ draftOf(f.id)?.verification?.categories ?? 0 }} 个分类 ·
              {{ draftOf(f.id)?.verification?.numbers ?? 0 }} 个数字 ·
              <span :class="draftOf(f.id)?.verification?.verified ? 'ok' : 'warn'">
                {{ draftOf(f.id)?.verification?.verified ? '全部可溯源' : '有待确认项' }}
              </span>
            </div>
            <div
              v-for="(cat, ci) in draftOf(f.id)?.categories ?? []"
              :key="cat.name"
              class="trace-row"
            >
              <span class="trace-row__name">{{ cat.name }}</span>
              <span class="trace-row__count">{{ cat.count }}</span>
              <el-button link type="primary" @click="showTrace(f, ci)">查看依据</el-button>
              <span v-if="!cat.verified" class="trace-row__warn">{{ cat.notes?.join('；') }}</span>
            </div>
            <div v-if="traceRows[f.id]?.length" class="trace-detail">
              <div v-for="r in traceRows[f.id]" :key="r.no">{{ r.text }}</div>
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
.trace-detail {
  margin: var(--space-1) 0 var(--space-2) var(--space-3);
  padding-left: var(--space-2);
  border-left: 2px solid var(--color-border-light, #e2e8f0);
  color: var(--color-text-secondary, #64748b);
}
</style>