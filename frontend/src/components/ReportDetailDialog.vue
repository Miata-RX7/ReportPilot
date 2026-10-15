<!--
  报告详情弹窗（自取数据）。

  原先是 ReportsView 内部的弹窗；报告管理页下线后，这里抽成组件，
  由「报告任务 → 执行记录」弹窗复用，避免详情能力丢失。
-->
<template>
  <el-dialog
    :model-value="reportId !== null"
    class="report-detail-dialog"
    width="min(1080px, 94vw)"
    top="4vh"
    :close-on-click-modal="false"
    destroy-on-close
    :title="detail ? reportName(titleFallback, detail.period) : '报告详情'"
    @update:model-value="onVisibleChange"
  >
    <div v-loading="loading" class="detail-body" data-testid="report-detail-dialog">
      <template v-if="detail">
        <div class="detail-meta">
          <span :class="['soft-tag', `soft-tag--${statusSoftClass(detail.status)}`]">
            <el-icon v-if="statusHasIcon(detail.status)" class="status-icon"><Check /></el-icon>
            {{ STATUS_LABEL[detail.status] }}
          </span>
          <el-tooltip
            v-if="isConfirmationExpired(detail)"
            content="确认链接已过期，可在报告任务里重新发送确认邮件"
            placement="top"
          >
            <span class="soft-tag soft-tag--warning">超期</span>
          </el-tooltip>
          <span class="text-muted">
            {{ detail.task_id ? `任务 #${detail.task_id} · ` : '手动生成 · ' }}
            {{ detail.created_at ? formatDateTime(detail.created_at) : '' }}
          </span>
        </div>

        <div v-if="detail.error_stage" class="detail-error">
          <el-alert
            type="error"
            show-icon
            :closable="false"
            :title="detail.error_stage === 'push' ? '推送失败' : '生成失败'"
            :description="detail.last_error || ''"
          />
        </div>

        <!-- AI 摘要（每个 AI 占位符各一段，可单独重新生成） -->
        <el-card data-testid="report-insights" class="detail-block">
          <template #header>
            <span class="block-title">
              AI 摘要
              <span v-if="insightSlots.length > 1" class="text-muted summary-sub">
                共 {{ insightSlots.length }} 段
              </span>
            </span>
          </template>

          <el-empty v-if="!insightSlots.length" description="本报告没有 AI 摘要" />

          <div
            v-for="seg in insightSlots"
            :key="seg.slot"
            class="insight-seg"
            data-testid="report-insight-seg"
          >
            <div class="insight-seg-head">
              <span v-if="insightSlots.length > 1" class="soft-tag">{{ seg.slot }}</span>
              <el-button
                size="small"
                text
                type="primary"
                :loading="regenSlot === seg.slot"
                data-testid="insight-regen"
                @click="regenerateInsight(seg.slot)"
              >
                重新生成
              </el-button>
              <el-button
                size="small"
                text
                type="info"
                data-testid="insight-ai-call"
                @click="openAiCall(seg.slot)"
              >
                AI 请求
              </el-button>
            </div>

            <el-alert
              v-if="seg.degraded"
              type="warning"
              show-icon
              :closable="false"
              title="AI 摘要生成失败，已用占位文本"
              class="insight-alert"
            />
            <el-alert
              v-if="seg.sample_leak?.length"
              type="error"
              show-icon
              :closable="false"
              class="insight-alert"
              :title="`该段照抄了参考范文中的数字：${seg.sample_leak.join('、')}，请人工核对`"
            />
            <div v-if="seg.unverified_numbers?.length" class="unverified-tip">
              以下数字未在事实数据中溯源，请人工核对：
              <el-tag
                v-for="(n, i) in seg.unverified_numbers"
                :key="i"
                type="warning"
                class="unverified-tag"
              >
                {{ n }}
              </el-tag>
            </div>

            <div class="insight-text">{{ seg.text || '—' }}</div>

            <el-collapse v-if="seg.prompt_used || seg.sample_used" class="insight-trace">
              <el-collapse-item title="本次使用的提示词与参考范文">
                <div class="trace-row">
                  <span class="text-muted">写作要求</span>
                  <p>{{ seg.prompt_used || '（未设置）' }}</p>
                </div>
                <div class="trace-row">
                  <span class="text-muted">参考范文</span>
                  <p>{{ seg.sample_used || '（未设置）' }}</p>
                </div>
              </el-collapse-item>
            </el-collapse>
            <el-tag v-if="seg.retried" type="warning" size="small">
              命中范文护栏（示例数字 / 条目数）后已自动纠正重试
            </el-tag>
          </div>
        </el-card>

        <!-- 事实数据 -->
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
                <!-- 换算后的单位：悬停可看原始口径（2026-09 这类长串与环比依据） -->
                <el-tooltip
                  v-else-if="rawValueHint(row)"
                  :content="rawValueHint(row)"
                  placement="top"
                >
                  <span class="cell-unit cell-unit--converted">{{ factDisplayUnit(row) }}</span>
                </el-tooltip>
                <span v-else class="cell-unit">{{ factDisplayUnit(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="基线值" width="140">
              <template #default="{ row }">
                <template v-if="factDisplayBaseline(row) !== null">
                  {{ formatFactValue(factDisplayBaseline(row) ?? 0, row.display_precision ?? null) }}
                  <span class="cell-unit">{{ factDisplayUnit(row) }}</span>
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
                    <div class="evidence-value">{{ periodLabel(row.period) }}</div>
                  </div>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 审计时间线 -->
        <el-card data-testid="report-audits" class="detail-block">
          <template #header><span class="block-title">执行记录</span></template>
          <el-empty v-if="!detail.audits.length" description="暂无执行记录" />
          <el-timeline v-else>
            <el-timeline-item
              v-for="a in detail.audits"
              :key="a.id"
              :timestamp="formatDateTime(a.created_at)"
            >
              <span :class="['soft-tag', `soft-tag--${auditActionSoftClass(a.action)}`]">
                {{ auditActionLabel(a.action) }}
              </span>
              <span v-if="a.auditor_id" class="audit-actor">操作人 #{{ a.auditor_id }}</span>
            </el-timeline-item>
          </el-timeline>
        </el-card>

        <!-- 底部操作 -->
        <div class="detail-actions">
          <el-button
            v-for="act in actions"
            :key="act"
            :type="act === 'retryPush' ? 'danger' : 'primary'"
            :plain="act === 'download'"
            :data-testid="`detail-action-${act}`"
            @click="onAction(act)"
          >
            {{ ACTION_LABEL[act] }}
          </el-button>
        </div>
      </template>
    </div>

    <!-- AI 请求留痕：这一段真正发给 AI 的 messages 与模型参数 -->
    <AiCallDrawer
      v-if="reportId !== null"
      v-model="aiCallVisible"
      :title="`AI 请求 · ${aiCallSlot ?? '全部段落'}`"
      :query="{ reportId: reportId ?? undefined, slot: aiCallSlot ?? undefined, limit: 50 }"
    />
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, PieChart, TrendCharts } from '@element-plus/icons-vue'

import { reportApi, type ReportDetailData } from '@/api/reports'
import type { InsightSlot } from '@/api/types'
import AiCallDrawer from '@/components/AiCallDrawer.vue'
import { useAuthStore } from '@/stores/auth'
import {
  ACTION_LABEL,
  auditActionLabel,
  auditActionSoftClass,
  countAbnormalFacts,
  isConfirmationExpired,
  reportName,
  statusActions,
  statusHasIcon,
  statusSoftClass,
  STATUS_LABEL,
  type ReportAction,
} from '@/utils/report'
import {
  changePctClass,
  factDisplayUnit,
  factValueText,
  formatChangePct,
  formatFactValue,
} from '@/utils/metric'
import { joinValueUnit } from '@/utils/unit'
import { formatDateTime, periodLabel } from '@/utils/format'
import { parseApiError } from '@/utils/error'
import { downloadBlob, filenameFromDisposition } from '@/utils/download'

const props = defineProps<{
  /** 要查看的报告 id；null 表示关闭 */
  reportId: number | null
  /** 模板名（列表行可传入，避免额外请求） */
  templateName?: string
}>()

const emit = defineEmits<{ (e: 'changed'): void }>()

const auth = useAuthStore()
const detail = ref<ReportDetailData | null>(null)
const loading = ref(false)

const titleFallback = computed(() => props.templateName || '报告')

function onVisibleChange(v: boolean) {
  if (!v) emit('changed')
}

/** 图表类型（报告里以图片呈现） */
function isChartShape(shape: string | undefined | null): boolean {
  return shape === 'series' || shape === 'pie'
}

function valueShapeLabel(shape: string): string {
  return shape === 'pie' ? '饼图' : '曲线图'
}

/** 报告快照里的 fact（宽松类型：facts_json 是历史快照，字段可能缺失） */
interface FactLikeRow {
  value?: unknown
  baseline_value?: unknown
  unit?: string | null
  display_baseline_value?: number | null
  display_unit?: string | null
  display_factor?: number | null
}

/** 基线展示值：换算后优先（与本期同一换算因子），缺省回退原始基线 */
function factDisplayBaseline(row: FactLikeRow): number | null {
  if (typeof row.display_baseline_value === 'number') return row.display_baseline_value
  return typeof row.baseline_value === 'number' ? row.baseline_value : null
}

/** 开启单位换算时给出原始口径提示（262468.9041666667 这类长串在这里，便于人工核对） */
function rawValueHint(row: FactLikeRow): string {
  const factor = typeof row.display_factor === 'number' ? row.display_factor : 1
  if (factor === 1 || typeof row.value !== 'number') return ''
  const unit = row.unit ?? ''
  const parts = [`原始值 ${joinValueUnit(String(row.value), unit)}`]
  if (typeof row.baseline_value === 'number') {
    parts.push(`基线 ${joinValueUnit(String(row.baseline_value), unit)}`)
  }
  return `${parts.join('，')}（环比与阈值判定基于此口径）`
}

const facts = computed(() => detail.value?.facts_json ?? [])
const abnormalCount = computed(() => countAbnormalFacts(facts.value))

/** AI 摘要各段（key = 槽位名，通常即占位符名）；按插入序展示 */
const insightSlots = computed(() => {
  const insights = (detail.value?.insights_json ?? {}) as Record<string, InsightSlot>
  return Object.entries(insights).map(([slot, value]) => ({ slot, ...value }))
})

/** 正在重生成中的槽位（用于按钮 loading，避免并发点同一段） */
const regenSlot = ref<string | null>(null)

/** AI 请求留痕抽屉：null = 不按槽位过滤（看该报告全部段落的请求） */
const aiCallVisible = ref(false)
const aiCallSlot = ref<string | null>(null)

/** 查看这一段真正发给 AI 的内容（messages / 模型参数 / 响应 / 护栏重试） */
function openAiCall(slot: string) {
  aiCallSlot.value = slot
  aiCallVisible.value = true
}

/**
 * 只重算这一段并就地替换展示。
 * 后端用报告快照重算文字并重渲染 docx（不重算指标），因此这里只需刷新该段。
 */
async function regenerateInsight(slot: string) {
  if (!detail.value) return
  regenSlot.value = slot
  try {
    const { data } = await reportApi.regenerateInsight(detail.value.id, slot)
    const insights = { ...((detail.value.insights_json ?? {}) as Record<string, InsightSlot>) }
    // 后端可能把旧槽位 key 迁移到当前绑定名（兼容升级前的报告），以返回值为准
    if (data.slot !== slot) delete insights[slot]
    insights[data.slot] = {
      ...(insights[data.slot] ?? { unverified_numbers: [], degraded: false }),
      text: data.text,
      unverified_numbers: data.unverified_numbers,
      sample_leak: data.sample_leak,
      degraded: data.degraded,
      retried: data.retried,
      model: data.model,
    }
    detail.value = { ...detail.value, insights_json: insights }
    ElMessage.success('该段摘要已重新生成')
    emit('changed')
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    regenSlot.value = null
  }
}

const actions = computed<ReportAction[]>(() => {
  if (!detail.value) return []
  const list = statusActions(detail.value.status)
  // 代确认是管理员特权，普通用户看不到（后端也会403 兜底）
  return list.filter((a) => a !== 'confirmByAdmin' || auth.isAdmin)
})

/** 变化率超阈值高亮 */
function factRowClass({ row }: { row: { metric_id: number } }): string {
  void row
  return ''
}

async function load(id: number) {
  loading.value = true
  detail.value = null
  try {
    detail.value = (await reportApi.detail(id)).data
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    loading.value = false
  }
}

watch(
  () => props.reportId,
  (id) => {
    if (id === null) {
      detail.value = null
      return
    }
    void load(id)
  },
  { immediate: true },
)

async function download(id: number, period: string) {
  try {
    const resp = await reportApi.download(id)
    downloadBlob(resp.data, filenameFromDisposition(resp.headers['content-disposition'], `${period}.docx`))
    ElMessage.success('报告已下载')
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}

async function onAction(act: ReportAction) {
  if (!detail.value) return
  const id = detail.value.id
  const period = detail.value.period
  const map: Record<string, () => Promise<unknown>> = {
    download: () => download(id, period),
    regenerate: () => reportApi.regenerate(id),
    retryPush: () => reportApi.retryPush(id),
    resendConfirmation: () => reportApi.resendConfirmation(id),
    confirmByAdmin: () => reportApi.confirmByAdmin(id),
  }
  const fn = map[act]
  if (!fn) return
  try {
    await fn()
    ElMessage.success(ACTION_LABEL[act] + '完成')
    // 状态可能变化，重新拉取
    await load(id)
    emit('changed')
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}
</script>

<style scoped lang="scss">
.detail-body {
  min-height: 200px;
}

.detail-meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.status-icon {
  margin-right: 2px;
}

.detail-error {
  margin-bottom: var(--space-3);
}

.detail-block {
  margin-bottom: var(--space-3);
}

.block-title {
  font-weight: 600;
}

.summary-sub {
  margin-left: var(--space-2);
  font-size: var(--font-xs);
  font-weight: 400;
}

.insight-alert,
.unverified-tip {
  margin-bottom: var(--space-2);
}

.unverified-tag {
  margin: 0 2px;
}

.insight-text {
  white-space: pre-wrap;
  line-height: 1.7;
}

/* 多段摘要：段与段之间用细线分隔 */
.insight-seg + .insight-seg {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.insight-seg-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.insight-trace {
  margin-top: 8px;

  .trace-row p {
    margin: 4px 0 10px;
    white-space: pre-wrap;
    color: var(--el-text-color-regular);
  }
}

.cell-unit {
  margin-left: 2px;
  font-size: var(--font-xs);
  color: var(--color-text-tertiary);
}

/* 换算后的单位：下划虚线提示可悬停查看原始口径 */
.cell-unit--converted {
  cursor: help;
  border-bottom: 1px dashed var(--el-border-color);
}

.chart-row-icon--series {
  color: var(--chart-series-1, #3b82f6);
}

.chart-row-icon--pie {
  color: #f59e0b;
}

.evidence {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2);
  padding: var(--space-2) 0;
}

.evidence-label {
  font-size: var(--font-xs);
  color: var(--color-text-tertiary);
}

.evidence-value {
  font-family: var(--code-font);
  font-size: var(--font-xs);
  word-break: break-all;
}

.audit-actor {
  margin-left: var(--space-2);
  font-size: var(--font-xs);
  color: var(--color-text-tertiary);
}

.detail-actions {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-3);
}
</style>