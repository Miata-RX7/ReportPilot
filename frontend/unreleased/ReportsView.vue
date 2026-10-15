<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { Check, PieChart, TrendCharts } from '@element-plus/icons-vue'

import { confirmDanger } from '@/components/ConfirmDialog'
import DataTable from '@/components/DataTable.vue'
import PageHeader from '@/components/PageHeader.vue'
import { reportApi, type ReportDetailData } from '@/api/reports'
import { templateApi } from '@/api/templates'
import type {
  InsightSlot,
  MetricFact,
  ReportListItem,
  ReportStatus,
  TemplateListItem,
} from '@/api/types'
import type { ReportAction } from '@/utils/report'
import {
  ACTION_LABEL,
  auditActionLabel,
  auditActionSoftClass,
  countAbnormalFacts,
  reportName,
  statusActions,
  statusHasIcon,
  statusSoftClass,
  STATUS_LABEL,
} from '@/utils/report'
import { changePctClass, factValueText, formatChangePct, formatFactValue } from '@/utils/metric'
import { formatDateTime, previousPeriod } from '@/utils/format'
import { parseApiError } from '@/utils/error'

/** 图表类型（报告里以图片呈现，facts 表里只给摘要行） */
function isChartShape(shape: string | undefined | null): boolean {
  return shape === 'series' || shape === 'pie'
}

function valueShapeLabel(shape: string | undefined | null): string {
  return shape === 'pie' ? '饼图' : '曲线图'
}
import { downloadBlob, filenameFromDisposition } from '@/utils/download'

const rows = ref<ReportListItem[]>([])
const loading = ref(false)
const statusFilter = ref<ReportStatus | ''>('')
const periodFilter = ref('')

const templates = ref<TemplateListItem[]>([])

/* ---- 生成弹窗 ---- */
const generateVisible = ref(false)
const generateSubmitting = ref(false)
const route = useRoute()
const router = useRouter()
const genFormRef = ref<FormInstance>()
const genError = ref<string | null>(null)
const genForm = reactive({
  template_id: null as number | null,
  period: previousPeriod(),
  recipient_emails: [] as string[],
  /** 确认人邮箱（必填）：确认链接会发到这里，确认后才推送给收件人 */
  confirmer_email: '',
})
const recipientInput = ref('')
/** 从"报告任务 → 执行记录"跳转过来时按任务过滤 */
const taskFilter = ref<number | undefined>(undefined)
const taskFilterName = ref<string | null>(null)
const MAX_RECIPIENTS = 20
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const genRules: FormRules = {
  template_id: [{ required: true, message: '请选择模板', trigger: 'change' }],
  period: [{ required: true, message: '请选择统计周期', trigger: 'change' }],
  confirmer_email: [
    { required: true, message: '请填写确认人邮箱', trigger: 'blur' },
    {
      validator: (_r, v, cb) =>
        !v || EMAIL_RE.test(String(v)) ? cb() : cb(new Error('邮箱格式不正确')),
      trigger: 'blur',
    },
  ],
}

/* ---- 详情抽屉 ---- */
const detailVisible = ref(false)
const detailId = ref<number | null>(null)
const detail = ref<ReportDetailData | null>(null)
const detailLoading = ref(false)
const detailTemplateName = ref('')

const summarySlot = computed<InsightSlot | null>(() => {
  const ij = detail.value?.insights_json
  if (!ij) return null
  if (ij.summary) return ij.summary
  const first = Object.values(ij)[0]
  return first ?? null
})

const facts = computed<MetricFact[]>(() => detail.value?.facts_json ?? [])
const abnormalCount = computed(() => countAbnormalFacts(facts.value))

/** unverified_numbers 涉及的 fact 行（按数值容差匹配 value/baseline_value），高亮提示 */
const highlightedMetricIds = computed(() => {
  const set = new Set<number>()
  const nums = (summarySlot.value?.unverified_numbers ?? [])
    .map((s) => {
      const m = s.replace(/%/g, '').match(/-?\d+(?:\.\d+)?/)
      if (!m) return null
      const n = Number(m[0])
      return Number.isNaN(n) ? null : n
    })
    .filter((n): n is number => n !== null)
  if (!nums.length) return set
  for (const f of facts.value) {
    const vals = [f.value, f.baseline_value].filter((v): v is number => v !== null)
    const hit = vals.some((v) =>
      nums.some((n) => Math.abs(v - n) <= Math.max(0.005, Math.abs(n) * 0.05)),
    )
    if (hit) set.add(f.metric_id)
  }
  return set
})

function factRowClass({ row }: { row: MetricFact }): string {
  return highlightedMetricIds.value.has(row.metric_id) ? 'fact-row--highlight' : ''
}

/** 列表行的可用操作（状态机） */
function rowActions(row: ReportListItem): ReportAction[] {
  return statusActions(row.status)
}

/** 从报告跳回任务详情 */
function gotoTask(taskId: number | null) {
  if (taskId === null) return
  void router.push({ name: 'reportTasks', query: { highlight: String(taskId) } })
}

/** 任务页跳转过来时带上任务过滤 */
function applyTaskFilterFromQuery() {
  const tid = route.query.task_id
  if (tid) taskFilter.value = Number(tid)
}

const detailActions = computed<ReportAction[]>(() =>
  detail.value ? statusActions(detail.value.status) : [],
)

async function load() {
  loading.value = true
  try {
    const { data } = await reportApi.list({
      status: statusFilter.value || undefined,
      period: periodFilter.value || undefined,
      taskId: taskFilter.value,
    })
    rows.value = data
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    loading.value = false
  }
}

/* ---- 生成 ---- */
async function openGenerate() {
  genError.value = null
  genForm.template_id = null
  genForm.period = previousPeriod()
  genForm.recipient_emails = []
  genForm.confirmer_email = ''
  recipientInput.value = ''
  generateSubmitting.value = false
  try {
    const { data } = await templateApi.list()
    templates.value = data
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
    return
  }
  generateVisible.value = true
  genFormRef.value?.clearValidate()
}

function addRecipient() {
  const raw = recipientInput.value.trim()
  recipientInput.value = ''
  if (!raw) return
  if (!EMAIL_RE.test(raw)) {
    ElMessage.warning('邮箱格式不正确')
    return
  }
  if (genForm.recipient_emails.includes(raw)) return
  if (genForm.recipient_emails.length >= MAX_RECIPIENTS) {
    ElMessage.warning(`收件人最多 ${MAX_RECIPIENTS} 个`)
    return
  }
  genForm.recipient_emails.push(raw)
}

function removeRecipient(idx: number) {
  genForm.recipient_emails.splice(idx, 1)
}

async function doGenerate() {
  const valid = await genFormRef.value?.validate().catch(() => false)
  if (!valid) return
  if (genForm.recipient_emails.length === 0) {
    ElMessage.warning('请至少添加一个收件人')
    return
  }
  generateSubmitting.value = true
  genError.value = null
  try {
    await reportApi.create({
      template_id: genForm.template_id as number,
      period: genForm.period,
      recipient_emails: [...genForm.recipient_emails],
      confirmer_email: genForm.confirmer_email,
    })
    ElMessage.success('报告已创建，正在生成（完成后需确认人确认才会推送）')
    generateVisible.value = false
    await load()
  } catch (error) {
    // 422（校验/模板）/ 502（数据源不可达）保留弹窗，展示错误详情
    genError.value = parseApiError(error).message
  } finally {
    generateSubmitting.value = false
  }
}

/* ---- 详情 ---- */
async function openDetail(row: ReportListItem) {
  detailId.value = row.id
  detailTemplateName.value = row.template_name
  detail.value = null
  detailLoading.value = true
  detailVisible.value = true
  try {
    const { data } = await reportApi.detail(row.id)
    detail.value = data
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    detailLoading.value = false
  }
}

async function downloadReport(id: number, period: string) {
  try {
    const resp = await reportApi.download(id)
    const blob = resp.data as Blob
    const disp = (resp.headers as Record<string, string>)['content-disposition']
    const filename = filenameFromDisposition(disp, `report_${id}_${period}.docx`)
    downloadBlob(blob, filename)
    ElMessage.success('报告已下载')
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}

/* ---- 状态机动作 ---- */
async function runAction(action: (id: number) => Promise<unknown>, id: number, okMsg: string) {
  try {
    await action(id)
    ElMessage.success(okMsg)
    const tasks: Promise<unknown>[] = [load()]
    if (detail.value && detailId.value === id) tasks.push(refreshDetail())
    await Promise.all(tasks)
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}

async function refreshDetail() {
  if (detailId.value === null) return
  try {
    const { data } = await reportApi.detail(detailId.value)
    detail.value = data
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}

async function onRegenerate(id: number) {
  await runAction((rid) => reportApi.regenerate(rid), id, '已重新生成，正在生成中')
}

async function onRetryPush(id: number) {
  await runAction((rid) => reportApi.retryPush(rid), id, '推送重试完成')
}

async function onResendConfirmation(id: number) {
  await runAction(
    (rid) => reportApi.resendConfirmation(rid),
    id,
    '确认邮件已重新发送（有效期已重置）',
  )
}

/** 列表/详情统一的动作分发 */
async function dispatch(action: ReportAction, row: ReportListItem) {
  switch (action) {
    case 'download':
      await downloadReport(row.id, row.period)
      break
    case 'regenerate':
      await onRegenerate(row.id)
      break
    case 'retryPush':
      await onRetryPush(row.id)
      break
    case 'resendConfirmation':
      await onResendConfirmation(row.id)
      break
  }
}


/** 抽屉底部按钮：下载走 download，其余复用 dispatch */
async function onDetailAction(action: ReportAction) {
  if (!detail.value) return
  const row: ReportListItem = {
    ...detail.value,
    template_name: '',
  }
  if (action === 'download') {
    await downloadReport(detail.value.id, detail.value.period)
    return
  }
  await dispatch(action, row)
}

onMounted(load)
</script>

<template>
  <div class="page-container">
    <PageHeader title="报告管理" description="生成、审核、推送月报（事实与 AI 洞察分离）">
      <template #actions>
        <el-button type="primary" @click="openGenerate">生成报告</el-button>
      </template>
    </PageHeader>

    <DataTable
      :data="rows"
      :loading="loading"
      empty-text="暂无报告，点击右上角生成"
    >
      <template #columns>
        <el-table-column label="报告名" min-width="220">
          <template #default="{ row }">
            <span class="report-name">{{ reportName(row.template_name, row.period) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="170">
          <template #default="{ row }">
            <span :class="['soft-tag', `soft-tag--${statusSoftClass(row.status)}`]">
              <el-icon v-if="statusHasIcon(row.status)" class="status-icon"><Check /></el-icon>
              {{ STATUS_LABEL[row.status as ReportStatus] }}
              <!-- Q12：超期时主标签保持原色，附"超期"提示 -->
              <el-tooltip
                v-if="isConfirmationExpired(row)"
                content="确认链接已过期，请联系管理员重新发送"
                placement="top"
              >
                <span class="expired-chip" data-testid="report-expired-chip">超期</span>
              </el-tooltip>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="130">
          <template #default="{ row }">
            <!-- Q16：任务触发给任务名（可跳转），手动给灰色"手动" -->
            <el-link
              v-if="row.task_name"
              type="primary"
              class="task-link"
              data-testid="report-source-task"
              @click="gotoTask(row.task_id)"
            >
              {{ row.task_name }}
            </el-link>
            <span v-else class="soft-tag soft-tag--neutral" data-testid="report-source-manual">手动</span>
          </template>
        </el-table-column>
        <el-table-column prop="period" label="周期" width="110" />
        <el-table-column label="收件人数" width="100">
          <template #default="{ row }">{{ row.recipient_emails?.length ?? 0 }}</template>
        </el-table-column>
        <el-table-column label="创建人" width="100">
          <template #default="{ row }">
            <span v-if="row.created_by">#{{ row.created_by }}</span>
            <span v-else class="text-muted">系统</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" data-testid="report-action-detail" @click="openDetail(row)">详情</el-button>
            <el-button
              v-for="act in rowActions(row)"
              :key="act"
              link
              type="primary"
              :class="{ 'is-default-action': act === 'resendConfirmation' && isConfirmationExpired(row) }"
              :data-testid="`report-action-${act}`"
              @click="dispatch(act, row)"
            >
              {{ ACTION_LABEL[act] }}
            </el-button>
          </template>
        </el-table-column>
      </template>

      <template #actions>
        <el-select
          v-model="statusFilter"
          placeholder="状态过滤"
          clearable
          class="filter-select"
          @change="load"
        >
          <el-option label="待生成" value="pending" />
          <el-option label="生成中" value="generating" />
          <el-option label="待确认" value="pending_confirmation" />
          <el-option label="已确认" value="confirmed" />
          <el-option label="已推送" value="pushed" />
          <el-option label="推送失败" value="push_failed" />
          <el-option label="生成失败" value="failed" />
        </el-select>
        <el-date-picker
          v-model="periodFilter"
          type="month"
          value-format="YYYY-MM"
          placeholder="周期过滤"
          clearable
          class="filter-select"
          @change="load"
        />
      </template>
    </DataTable>

    <!-- 生成弹窗 -->
    <FormDialog
      v-model:visible="generateVisible"
      title="生成报告"
      :submitting="generateSubmitting"
      width="560px"
      @submit="doGenerate"
    >
      <el-form ref="genFormRef" :model="genForm" :rules="genRules" label-width="90px" data-testid="report-generate-dialog">
        <el-form-item label="模板" prop="template_id">
          <el-select v-model="genForm.template_id" placeholder="选择模板" class="full-width">
            <el-option v-for="t in templates" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="统计周期" prop="period">
          <el-date-picker
            v-model="genForm.period"
            type="month"
            value-format="YYYY-MM"
            placeholder="选择月份"
            :clearable="false"
          />
        </el-form-item>
        <el-form-item label="收件人">
          <div class="tag-input">
            <el-tag
              v-for="(r, i) in genForm.recipient_emails"
              :key="r"
              closable
              @close="removeRecipient(i)"
            >
              {{ r }}
            </el-tag>
            <el-input
              v-model="recipientInput"
              placeholder="输入邮箱后回车添加"
              @keydown.enter.prevent="addRecipient"
              @blur="addRecipient"
            />
          </div>
          <span class="text-muted form-hint">EmailStr 格式，去重，上限 {{ MAX_RECIPIENTS }} 个</span>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="genError"
        :title="genError"
        type="error"
        show-icon
        :closable="false"
        class="gen-alert"
      />

      <div v-if="generateSubmitting" class="gen-hint">
        生成中，约 30 秒~2 分钟，请勿关闭页面
      </div>
    </FormDialog>

    <!-- 详情抽屉 -->
    <!-- 详情弹窗（宽屏；抽屉会浪费横向空间，facts 表格被压窄） -->
    <el-dialog
      v-model="detailVisible"
      class="report-detail-dialog"
      width="min(1080px, 94vw)"
      top="4vh"
      :close-on-click-modal="false"
      destroy-on-close
      :title="detail ? reportName(detailTemplateName || '报告', detail.period) : '报告详情'"
    >
      <div v-loading="detailLoading" class="detail-body" data-testid="report-detail-dialog">
        <template v-if="detail">
          <div class="detail-meta">
            <span :class="['soft-tag', `soft-tag--${statusSoftClass(detail.status)}`]">
              <el-icon v-if="statusHasIcon(detail.status)" class="status-icon"><Check /></el-icon>
              {{ STATUS_LABEL[detail.status] }}
            </span>
            <span class="text-muted">创建人 #{{ detail.created_by }} · {{ formatDateTime(detail.created_at) }}</span>
          </div>

          <div v-if="detail.error_stage" class="detail-error">
            <el-alert
              type="error"
              show-icon
              :closable="false"
              :title="`${detail.error_stage === 'push' ? '推送' : '生成'}失败`"
              :description="detail.last_error || ''"
            />
          </div>

          <!-- AI 摘要卡片（置顶） -->
          <el-card data-testid="report-insights" class="detail-block">
            <template #header><span class="block-title">AI 摘要</span></template>
            <el-alert
              v-if="summarySlot?.degraded"
              type="warning"
              show-icon
              :closable="false"
              title="AI 摘要生成失败，已用占位文本"
              class="insight-alert"
            />
            <div v-if="summarySlot?.unverified_numbers?.length" class="unverified-tip">
              以下数字未在事实数据中溯源，请人工核对：
              <el-tag
                v-for="(n, i) in summarySlot.unverified_numbers"
                :key="i"
                type="warning"
                class="unverified-tag"
              >{{ n }}</el-tag>
            </div>
            <div class="insight-text">{{ summarySlot?.text || '—' }}</div>
          </el-card>

          <!-- facts 折叠区 -->
          <el-card data-testid="report-facts" class="detail-block">
            <template #header>
              <span class="block-title">
                事实数据
                <span class="text-muted summary-sub">
                  共 {{ facts.length }} 个指标，{{ abnormalCount }} 个异常（|change_pct| &gt; 10%）
                </span>
              </span>
            </template>
            <el-table :data="facts" row-key="metric_id" :row-class-name="factRowClass">
              <el-table-column label="" width="46">
                <template #default="{ row }">
                  <el-tooltip
                    v-if="isChartShape(row.value_shape)"
                    :content="`${valueShapeLabel(row.value_shape)}：该指标在报告中以图片呈现`"
                    placement="top"
                  >
                    <el-icon
                      class="chart-row-icon"
                      :class="`chart-row-icon--${row.value_shape}`"
                      :data-testid="`fact-chart-icon-${row.value_shape}`"
                    >
                      <PieChart v-if="row.value_shape === 'pie'" />
                      <TrendCharts v-else />
                    </el-icon>
                  </el-tooltip>
                </template>
              </el-table-column>
              <el-table-column prop="name" label="指标" min-width="160" />
              <el-table-column label="值" width="140">
                <template #default="{ row }">
                  {{ factValueText(row) }}
                  <span v-if="row.value_shape && row.value_shape !== 'scalar'" class="cell-unit">
                    {{ row.value_shape }}
                  </span>
                  <span v-else class="cell-unit">{{ row.unit }}</span>
                </template>
              </el-table-column>
              <el-table-column label="基线值" width="140">
                <template #default="{ row }">
                  <template v-if="row.baseline_value !== null">
                    {{ formatFactValue(row.baseline_value, row.display_precision ?? null) }}
                    <span class="cell-unit">{{ row.unit }}</span>
                  </template>
                  <span v-else>—</span>
                </template>
              </el-table-column>
              <el-table-column label="变化率" width="110">
                <template #default="{ row }">
                  <span :class="['soft-tag', `soft-tag--${changePctClass(row.change_pct)}`]">
                    {{ formatChangePct(row.change_pct) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column type="expand" label="证据" width="70">
                <template #default="{ row }">
                  <div class="evidence">
                    <div class="evidence-item">
                      <div class="evidence-label">来源系统</div>
                      <div class="evidence-value">{{ row.source }}</div>
                    </div>
                    <div class="evidence-item">
                      <div class="evidence-label">原始对象</div>
                      <div class="evidence-value">{{ row.object }}</div>
                    </div>
                    <div class="evidence-item">
                      <div class="evidence-label">计算公式</div>
                      <div class="evidence-value">{{ row.formula }}</div>
                    </div>
                    <div class="evidence-item">
                      <div class="evidence-label">统计周期</div>
                      <div class="evidence-value">{{ row.period }}</div>
                    </div>
                  </div>
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <!-- 审核时间线 -->
          <el-card data-testid="report-audits" class="detail-block">
            <template #header><span class="block-title">审核记录</span></template>
            <el-empty v-if="!detail.audits.length" description="暂无审核记录" />
            <el-timeline v-else>
              <el-timeline-item
                v-for="a in detail.audits"
                :key="a.id"
                :timestamp="formatDateTime(a.created_at)"
              >
                <span :class="['soft-tag', `soft-tag--${auditActionSoftClass(a.action)}`]">
                  {{ auditActionLabel(a.action) }}
                </span>
                <span class="audit-actor">操作人 #{{ a.auditor_id }}</span>
                <div v-if="a.comment" class="audit-comment">{{ a.comment }}</div>
              </el-timeline-item>
            </el-timeline>
          </el-card>

          <!-- 底部操作区 -->
          <div class="detail-actions">
            <el-button
              v-for="act in detailActions"
              :key="act"
              :type="act === 'retryPush' ? 'danger' : 'primary'"
              :plain="act === 'download'"
              :data-testid="`detail-action-${act}`"
              @click="onDetailAction(act)"
            >
              {{ ACTION_LABEL[act] }}
            </el-button>
          </div>
        </template>
      </div>
    </el-dialog>

  </div>
</template>

<style scoped lang="scss">
.full-width {
  width: 100%;
}

.form-hint {
  margin-left: var(--space-2);
  font-size: var(--font-xs);
}

.filter-select {
  width: 160px;
}

.report-name {
  font-weight: 500;
}

.status-icon {
  margin-right: 2px;
}

.gen-alert {
  margin-top: var(--space-3);
}

.gen-hint {
  margin-top: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--tag-radius);
  background-color: var(--color-info-bg);
  color: var(--color-info);
  font-size: var(--font-sm);
}

.tag-input {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
  width: 100%;
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--input-radius);
  background-color: var(--color-bg-elevated);
}

.tag-input .el-input {
  flex: 1;
  min-width: 160px;
}

.detail-body {
  min-height: 200px;
}

.detail-block {
  margin-bottom: var(--space-4);
  border-radius: var(--card-radius);
}

.detail-meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.detail-error {
  margin-bottom: var(--space-3);
}

.block-title {
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--color-text-secondary);
}

.summary-sub {
  font-weight: 400;
  margin-left: var(--space-2);
}

.insight-alert {
  margin-bottom: var(--space-3);
}

.unverified-tip {
  margin-bottom: var(--space-2);
  font-size: var(--font-sm);
  color: var(--color-warning);
}

.unverified-tag {
  margin-left: var(--space-2);
}

.insight-text {
  margin-top: var(--space-2);
  font-size: var(--font-sm);
  line-height: 1.7;
  white-space: pre-wrap;
  color: var(--color-text-primary);
}

.cell-unit {
  margin-left: 2px;
  font-size: var(--font-xs);
  color: var(--color-text-tertiary);
}

:deep(.fact-row--highlight) {
  background-color: var(--color-warning-bg);
}

.evidence-item {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  font-size: var(--font-sm);
}

.evidence-label {
  flex-shrink: 0;
  width: 80px;
  color: var(--color-text-tertiary);
}

.evidence-value {
  color: var(--color-text-primary);
  word-break: break-all;
}

.audit-actor {
  margin-left: var(--space-2);
  font-size: var(--font-sm);
  color: var(--color-text-tertiary);
}

.audit-comment {
  margin-top: var(--space-1);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}

/* 详情弹窗：内容可滚动，避免长 facts 表格顶出视口 */
.report-detail-dialog :deep(.el-dialog__body) {
  max-height: 78vh;
  overflow-y: auto;
}

.chart-row-icon {
  font-size: 15px;
  cursor: help;
}

/* 曲线=品牌蓝，饼图=语义琥珀（与报告里饼图的 UP/DOWN 语义色区分开） */
.chart-row-icon--series {
  color: var(--chart-series-1, #3b82f6);
}

.chart-row-icon--pie {
  color: #f59e0b;
}

.detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}
</style>
