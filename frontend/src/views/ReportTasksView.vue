<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { MoreFilled } from '@element-plus/icons-vue'

import PageHeader from '@/components/PageHeader.vue'
import ReportDetailDialog from '@/components/ReportDetailDialog.vue'
import { reportTaskApi } from '@/api/reportTasks'
import { reportApi } from '@/api/reports'
import { templateApi } from '@/api/templates'
import { userApi } from '@/api/users'
import type {
  ReportListItem,
  ReportStatus,
  ReportTaskRead,
  TemplateListItem,
  UserRead,
} from '@/api/types'
import { CRON_PRESETS, cronSummary, isValidCron, nextRunPreview } from '@/utils/cron'
import {
  ACTION_LABEL,
  isConfirmationExpired,
  statusActions,
  statusSoftClass,
  STATUS_LABEL,
  type ReportAction,
} from '@/utils/report'
import { downloadBlob, filenameFromDisposition } from '@/utils/download'
import { parseApiError } from '@/utils/error'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime, periodLabel, previousPeriod, previousWeek } from '@/utils/format'

const rows = ref<ReportTaskRead[]>([])
const auth = useAuthStore()
const loading = ref(false)
const templates = ref<TemplateListItem[]>([])
const users = ref<UserRead[]>([])

// ---------------- 新建/编辑弹窗 ----------------
const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)
const form = reactive({
  name: '',
  template_id: null as number | null,
  cron_expr: '0 9 1 * *',
  cron_preset: '0 9 1 * *',
  // 统计周期：current=本期（触发时刻所在周期）/ last_week=上周 / last_month=上月
  period_preset: 'current',
  confirmer_email: '',
  recipient_emails: [] as string[],
  is_active: true,
})

/** 统计周期预设 -> 后端 (period_granularity, period_offset) */
const PERIOD_PRESETS = [
  { value: 'current', label: '本期（按触发时间）', granularity: 'month', offset: 0 },
  { value: 'last_week', label: '上周', granularity: 'week', offset: -1 },
  { value: 'last_month', label: '上月', granularity: 'month', offset: -1 },
] as const

type PeriodPreset = (typeof PERIOD_PRESETS)[number]['value']

const periodHint = computed(() => {
  if (form.period_preset === 'last_week') {
    return '取上一个完整自然周（周一 ~ 周日），按周报生成'
  }
  if (form.period_preset === 'last_month') {
    return '取上一个完整自然月，按月报生成'
  }
  return '取触发时刻所在的统计周期（等同升级前的默认行为）'
})

function periodRuleOf(preset: string) {
  const p = PERIOD_PRESETS.find((x) => x.value === preset) ?? PERIOD_PRESETS[0]
  return { period_granularity: p.granularity, period_offset: p.offset }
}

/** 回填：把任务已保存的粒度/偏移映射回下拉预设 */
function presetOf(row: ReportTaskRead): PeriodPreset {
  const granularity = row.period_granularity ?? 'month'
  const offset = row.period_offset ?? 0
  if (granularity === 'week' && offset === -1) return 'last_week'
  if (granularity === 'month' && offset === -1) return 'last_month'
  return 'current'
}

/** 任务列表里统计周期的人读描述：如 "月报 · 本期" / "周报 · 上周" */
function periodKindText(row: ReportTaskRead): string {
  const kind = (row.period_granularity ?? 'month') === 'week' ? '周报' : '月报'
  const preset = presetOf(row)
  const label = PERIOD_PRESETS.find((p) => p.value === preset)?.label ?? '本期'
  return `${kind} · ${label.replace('（按触发时间）', '')}`
}

const activeTemplates = computed(() => templates.value.filter((t) => t.is_active))

/** 确认人下拉：优先平台用户（按邮箱匹配），同时允许手填外部邮箱 */

const cronValid = computed(() => isValidCron(form.cron_expr))
const cronNext = computed(() => nextRunPreview(form.cron_expr))
const cronText = computed(() => (cronValid.value ? cronSummary(form.cron_expr) : '表达式无效'))

function onPresetChange(v: string) {
  form.cron_preset = v
  form.cron_expr = v
}

function openCreate() {
  editingId.value = null
  form.name = ''
  form.template_id = activeTemplates.value[0]?.id ?? null
  form.cron_expr = '0 9 1 * *'
  form.cron_preset = '0 9 1 * *'
  form.period_preset = 'current'
  form.confirmer_email = ''
  form.recipient_emails = []
  form.is_active = true
  dialogVisible.value = true
}

function openEdit(row: ReportTaskRead) {
  editingId.value = row.id
  form.name = row.name
  form.template_id = row.template_id
  form.cron_expr = row.cron_expr
  form.cron_preset = ''
  form.period_preset = presetOf(row)
  form.confirmer_email = row.confirmer_email
  // 后端已回传收件人清单 -> 原样回填，改完只覆盖收件人，不会再"顺手清空"
  form.recipient_emails = [...(row.recipient_emails ?? [])]
  form.is_active = row.is_active
  dialogVisible.value = true
}

async function save() {
  if (!form.name.trim()) return ElMessage.warning('请填写任务名称')
  if (!form.template_id) return ElMessage.warning('请选择模板')
  if (!cronValid.value) return ElMessage.warning('cron 表达式无效（需 5 段：分 时 日 月 周）')
  if (!form.confirmer_email.trim()) return ElMessage.warning('请填写确认人邮箱')
  // 编辑时已回填原有收件人，"空"只可能是被用户删掉了 —— 后端要求至少 1 个
  if (form.recipient_emails.length === 0) return ElMessage.warning('请至少填写 1 个收件人邮箱')

  saving.value = true
  try {
    if (editingId.value) {
      const payload: Record<string, unknown> = {
        name: form.name.trim(),
        template_id: form.template_id,
        cron_expr: form.cron_expr,
        ...periodRuleOf(form.period_preset),
        confirmer_email: form.confirmer_email.trim(),
        // 回填后这里是完整清单（含原有收件人），可以整体覆盖
        recipient_emails: form.recipient_emails,
        is_active: form.is_active,
      }
      await reportTaskApi.update(editingId.value, payload)
      ElMessage.success('任务已更新，调度已重排')
    } else {
      await reportTaskApi.create({
        name: form.name.trim(),
        template_id: form.template_id as number,
        cron_expr: form.cron_expr,
        ...periodRuleOf(form.period_preset),
        confirmer_email: form.confirmer_email.trim(),
        recipient_emails: form.recipient_emails,
        is_active: form.is_active,
      })
      ElMessage.success('任务已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    saving.value = false
  }
}

// ---------------- 行操作 ----------------
async function onToggleActive(row: ReportTaskRead) {
  try {
    await reportTaskApi.setActive(row.id, !row.is_active)
    ElMessage.success(row.is_active ? '任务已停用（不再产生新实例）' : '任务已启用')
    await load()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}

async function onRun(row: ReportTaskRead) {
  try {
    await ElMessageBox.confirm(
      `立即执行一次「${row.name}」？将生成报告并向确认人 ${row.confirmer_email} 发送确认邮件。`,
      '手动触发',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    const { data } = await reportTaskApi.run(row.id)
    if (data.status === 'created') {
      ElMessage.success(`已触发，正在生成报告（实例 #${data.report_id}）`)
      setTimeout(load, 1500)
    } else {
      ElMessage.warning(data.reason ?? '未触发')
    }
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}

async function onDelete(row: ReportTaskRead) {
  // 删除会连带抹掉执行历史与生成的文件，不可逆 —— 所以要求**手动输入任务名**确认，
  // 而不只是点一下「确定」。
  try {
    await ElMessageBox.prompt(
      `平台将会删除月报任务「${row.name}」及相关历史文件，请再次确认。`,
      '删除任务',
      {
        type: 'warning',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        inputPlaceholder: `请输入任务名「${row.name}」`,
        inputValidator: (val: string) =>
          val?.trim() === row.name || '输入的任务名与要删除的任务不一致',
        closeOnClickModal: false,
      },
    )
  } catch {
    return
  }
  try {
    await reportTaskApi.remove(row.id)
    ElMessage.success('任务已删除')
    await load()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}

/* ---- 执行记录（弹窗，仅列最近 RECENT_LIMIT 条） ---- */
const historyVisible = ref(false)
const historyLoading = ref(false)
const historyTask = ref<ReportTaskRead | null>(null)
const historyRows = ref<ReportListItem[]>([])
const detailId = ref<number | null>(null)
/** 每个任务只保留最近 10 个报告 */
const RECENT_LIMIT = 10

async function viewHistory(row: ReportTaskRead) {
  historyTask.value = row
  historyRows.value = []
  historyVisible.value = true
  await loadHistory()
}

async function loadHistory() {
  if (!historyTask.value) return
  historyLoading.value = true
  try {
    const { data } = await reportApi.list({
      taskId: historyTask.value.id,
      limit: RECENT_LIMIT,
    })
    historyRows.value = data
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    historyLoading.value = false
  }
}

/** 状态机允许的动作（与报告详情同一份词表）。
 *
 * 待确认时的"确认并发送"按钮只给两种人：
 * - 该任务的确认人本人（登录邮箱 == 任务 confirmer_email）
 * - 管理员（代确认）
 * 其他人看不到任何确认入口。 */
function historyActions(row: ReportListItem, task: ReportTaskRead | null) {
  const list = statusActions(row.status)
  if (row.status !== 'pending_confirmation') return list
  const mine = task !== null && auth.email === task.confirmer_email
  return mine || auth.isAdmin ? list : list.filter((a) => a !== 'confirmByAdmin')
}

async function onHistoryAction(act: ReportAction, row: ReportListItem) {
  const map: Record<string, () => Promise<unknown>> = {
    download: () => downloadReport(row.id, row.period),
    regenerate: () => reportApi.regenerate(row.id),
    retryPush: () => reportApi.retryPush(row.id),
    resendConfirmation: () => reportApi.resendConfirmation(row.id),
    confirmByAdmin: () => reportApi.confirmByAdmin(row.id),
  }
  const fn = map[act]
  if (!fn) return
  try {
    await fn()
    ElMessage.success(confirmMessage(act))
    await loadHistory()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}

/** 代确认/重发/重推各自的提示文案 */
function confirmMessage(act: ReportAction): string {
  if (act === 'confirmByAdmin') return '已代确认，报告已发送'
  if (act === 'resendConfirmation') return '确认邮件已重新发送'
  if (act === 'retryPush') return '推送重试完成'
  return `${ACTION_LABEL[act]}完成`
}

/** 三点菜单：停用/启用、删除 */
function onMoreAction(cmd: string, row: ReportTaskRead) {
  if (cmd === 'toggle') {
    void onToggleActive(row)
  } else if (cmd === 'delete') {
    void onDelete(row)
  }
}

/** 下载报告 docx */
async function downloadReport(id: number, period: string) {
  const resp = await reportApi.download(id)
  downloadBlob(
    resp.data,
    filenameFromDisposition(resp.headers['content-disposition'], `${period}.docx`),
  )
  ElMessage.success('报告已下载')
}

/** 空态里直接生成一份（等价于"手动触发"，周期=上一个统计周期，按任务粒度取整月/整周） */
async function generateFirst() {
  if (!historyTask.value) return
  const period =
    historyTask.value.period_granularity === 'week' ? previousWeek() : previousPeriod()
  try {
    await reportTaskApi.run(historyTask.value.id, period)
    ElMessage.success(`已触发生成（周期 ${periodLabel(period)}），稍后刷新查看`)
    historyVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}

/** 下拉项文案：显示名（登录名）·邮箱 */
function userOptionLabel(u: UserRead): string {
  const name = u.display_name || u.username
  return `${name}（${u.username}）· ${u.email}`
}

async function load() {
  loading.value = true
  try {
    const [t, u] = await Promise.all([reportTaskApi.list(), userApi.list()])
    rows.value = t.data
    users.value = u.data
    if (!templates.value.length) {
      templates.value = (await templateApi.list()).data
    }
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-container">
    <PageHeader title="报告任务" description="周期性生成报告：到点生成 → 确认人确认 → 自动推送">
      <template #actions>
        <el-button type="primary" data-testid="task-create" @click="openCreate">新建任务</el-button>
      </template>
    </PageHeader>

    <el-alert
      v-if="rows.some((r) => r.pending_confirmation > 0)"
      type="warning"
      show-icon
      :closable="false"
      class="pending-alert"
      data-testid="task-pending-alert"
      :title="`有任务存在待确认实例，请及时提醒确认人点击确认链接（链接 ${'72'} 小时后过期）`"
    />

    <el-table v-loading="loading" :data="rows" empty-text="暂无报告任务">
      <el-table-column label="任务名" min-width="160">
        <template #default="{ row }">
          <div>{{ row.name }}</div>
          <div v-if="row.last_error" class="text-muted err-line" :title="row.last_error">
            {{ row.last_error }}
          </div>
        </template>
      </el-table-column>
      <el-table-column label="模板" min-width="140">
        <template #default="{ row }">{{ row.template_name ?? `#${row.template_id}` }}</template>
      </el-table-column>
      <el-table-column label="周期" min-width="200">
        <template #default="{ row }">
          <div>{{ cronSummary(row.cron_expr) }}</div>
          <div class="text-muted mono">{{ row.cron_expr }}</div>
          <div class="text-muted period-kind" data-testid="task-period-kind">
            {{ periodKindText(row) }}
          </div>
        </template>
      </el-table-column>
      <el-table-column label="确认人" min-width="180">
        <template #default="{ row }">{{ row.confirmer_email }}</template>
      </el-table-column>
      <el-table-column label="收件人" width="90" align="center">
        <template #default="{ row }">{{ row.recipient_count }}</template>
      </el-table-column>
      <el-table-column label="待确认" width="90" align="center">
        <template #default="{ row }">
          <span v-if="row.pending_confirmation > 0" class="warn-num">
            {{ row.pending_confirmation }}
          </span>
          <span v-else class="text-muted">0</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <span
            :class="['soft-tag', row.is_active ? 'soft-tag--success' : 'soft-tag--neutral']"
            :data-testid="`task-active-${row.is_active}`"
          >
            {{ row.is_active ? '启用' : '停用' }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="下次执行" min-width="150">
        <template #default="{ row }">
          <div v-if="row.is_active">{{ formatDateTime(row.next_run_at) }}</div>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="250" fixed="right">
        <template #default="{ row }">
          <!-- 与模板管理页一致：flex + align-items:center 保证三点与文字垂直居中、间距统一 -->
          <div class="action-cell">
            <el-button link type="primary" data-testid="task-action-edit" @click="openEdit(row)">
              编辑
            </el-button>
            <el-button link type="primary" data-testid="task-action-run" @click="onRun(row)">
              手动触发
            </el-button>
            <el-button link type="primary" data-testid="task-action-history" @click="viewHistory(row)">
              执行记录
            </el-button>
            <!-- 停用 / 删除收进三点菜单（与模板管理一致），避免操作列过宽 -->
            <el-dropdown trigger="click" @command="(cmd: string) => onMoreAction(cmd, row)">
              <el-button link type="primary" data-testid="task-action-more">
                <el-icon><MoreFilled /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="toggle" data-testid="task-more-toggle">
                    {{ row.is_active ? '停用' : '启用' }}
                  </el-dropdown-item>
                  <el-dropdown-item
                    command="delete"
                    divided
                    class="danger-item"
                    data-testid="task-more-delete"
                  >
                    删除
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑任务' : '新建任务'"
      width="720px"
      top="6vh"
      destroy-on-close
    >
      <el-form label-width="100px" class="task-form" @submit.prevent>
        <el-form-item label="任务名称" required>
          <el-input
            v-model="form.name"
            placeholder="如：每月运维月报"
            data-testid="task-form-name"
          />
        </el-form-item>
        <el-form-item label="模板" required>
          <el-select
            v-model="form.template_id"
            class="w-full"
            placeholder="选择启用的模板"
            data-testid="task-form-template"
          >
            <el-option
              v-for="t in activeTemplates"
              :key="t.id"
              :label="t.name"
              :value="t.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="周期规则" required>
          <div class="cron-row">
            <el-select
              :model-value="form.cron_preset"
              class="cron-preset"
              placeholder="常用预设"
              data-testid="task-cron-preset"
              @change="onPresetChange"
            >
              <el-option
                v-for="p in CRON_PRESETS"
                :key="p.value"
                :label="p.label"
                :value="p.value"
              />
            </el-select>
            <el-input
              v-model="form.cron_expr"
              placeholder="分 时 日 月 周，如 0 9 1 * *"
              data-testid="task-cron-expr"
            />
          </div>
          <div class="text-muted cron-hint" data-testid="task-cron-hint">
            <span v-if="cronValid">
              {{ cronText }} ｜ 下次执行 {{ cronNext }}
            </span>
            <span v-else class="warn-text">cron 表达式无效（需 5 段：分 时 日 月 周）</span>
          </div>
        </el-form-item>
        <el-form-item label="统计周期" required>
          <div class="period-row">
            <el-select
              v-model="form.period_preset"
              class="period-preset"
              data-testid="task-period-preset"
            >
              <el-option
                v-for="p in PERIOD_PRESETS"
                :key="p.value"
                :label="p.label"
                :value="p.value"
              />
            </el-select>
            <span class="text-muted form-hint" data-testid="task-period-hint">
              {{ periodHint }}
            </span>
          </div>
        </el-form-item>
        <el-form-item label="确认人" required>
          <el-select
            v-model="form.confirmer_email"
            class="w-full"
            filterable
            placeholder="选择平台用户（确认人）"
            data-testid="task-form-confirmer"
          >
            <el-option
              v-for="u in users"
              :key="u.id"
              :label="userOptionLabel(u)"
              :value="u.email"
            />
          </el-select>
          <span class="text-muted form-hint">
            生成完成后向该用户邮箱发送一次性确认链接；该用户登录平台后也可在「执行记录」里直接确认
          </span>
        </el-form-item>
        <el-form-item label="收件人" required>
          <el-select
            v-model="form.recipient_emails"
            class="w-full"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="从列表选择，或直接输入邮箱后回车"
            data-testid="task-form-recipients"
          >
            <!-- 与「确认人」同一份用户清单；allow-create 让外部邮箱也能手填 -->
            <el-option
              v-for="u in users"
              :key="u.id"
              :label="userOptionLabel(u)"
              :value="u.email"
            />
          </el-select>
          <span class="text-muted form-hint">
            可从上方列表选，也可直接输入外部邮箱后回车；至少 1 个，报告推送时发给他们
          </span>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.is_active" data-testid="task-form-active" />
          <span class="text-muted form-hint">停用后不再按 cron 产生新实例，已有实例不受影响</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" data-testid="task-form-save" @click="save">
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>

    <!-- 执行记录弹窗：每个任务只列最近 10 个报告 -->
    <el-dialog
      v-model="historyVisible"
      width="min(1080px, 94vw)"
      top="6vh"
      :close-on-click-modal="false"
      destroy-on-close
      class="task-history-dialog"
      data-testid="task-history-dialog"
      :title="historyTask ? `执行记录 · ${historyTask.name}（最近 ${RECENT_LIMIT} 条）` : '执行记录'"
    >
      <div v-loading="historyLoading">
        <el-alert
          v-if="historyRows.length >= RECENT_LIMIT"
          type="info"
          show-icon
          :closable="false"
          class="limit-hint"
          :title="`仅显示最近 ${RECENT_LIMIT} 条报告，更早的记录不再展示`"
        />

        <el-empty
          v-if="!historyLoading && historyRows.length === 0"
          description="该任务还没有生成过报告"
        >
          <el-button type="primary" data-testid="task-history-generate" @click="generateFirst">
            立即生成一份
          </el-button>
        </el-empty>

        <el-table
          v-else
          :data="historyRows"
          empty-text="暂无报告"
          @row-click="(row: ReportListItem) => (detailId = row.id)"
        >
          <el-table-column label="周期" min-width="150">
            <template #default="{ row }" data-testid="history-period">
              <span :title="row.period">{{ periodLabel(row.period) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="150">
            <template #default="{ row }">
              <span :class="['soft-tag', `soft-tag--${statusSoftClass(row.status)}`]">
                {{ STATUS_LABEL[row.status as ReportStatus] }}
              </span>
              <el-tag
                v-if="isConfirmationExpired(row)"
                type="warning"
                size="small"
                class="expired-tag"
                data-testid="history-expired-tag"
              >
                超期
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="来源" width="90">
            <template #default="{ row }">
              <span class="text-muted">{{ row.source === 'cron' ? '定时' : '手动' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="收件人数" width="90">
            <template #default="{ row }">{{ row.recipient_emails?.length ?? 0 }}</template>
          </el-table-column>
          <el-table-column label="生成时间" width="170">
            <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="368" fixed="right">
            <template #default="{ row }">
              <!-- 一行放不下就横向滚动，按钮不换行 -->
              <div class="row-actions">
                <el-button
                  link
                  type="primary"
                  :data-testid="`history-action-detail-${row.period}`"
                  @click.stop="detailId = row.id"
                >
                  详情
                </el-button>
                <el-button
                  v-for="act in historyActions(row, historyTask)"
                  :key="act"
                  link
                  :type="act === 'retryPush' ? 'danger' : 'primary'"
                  :data-testid="`history-action-${act}-${row.period}`"
                  @click.stop="onHistoryAction(act, row)"
                >
                  {{ ACTION_LABEL[act] }}
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <div class="history-foot">
          <span class="text-muted">点击行可查看报告详情（AI 摘要 / 事实数据 / 执行记录）</span>
          <el-button data-testid="task-history-refresh" @click="loadHistory">刷新</el-button>
        </div>
      </div>
    </el-dialog>

    <!-- 报告详情（子弹窗，含下载 / 重试推送 / 重发确认） -->
    <ReportDetailDialog :report-id="detailId" @changed="detailId = null; loadHistory()" />
</template>

<style scoped lang="scss">
/* 操作列：按钮 / 三点菜单统一 flex 对齐（与模板管理页 action-cell 一致） */
.action-cell {
  display: flex;
  align-items: center;
  gap: var(--space-3);

  .el-button {
    margin-left: 0;
  }
}

/* 操作列按钮不换行 */
.row-actions {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: var(--space-1);
  white-space: nowrap;
}

/* 三点菜单里的"删除"（与模板管理页保持一致） */
:deep(.danger-item) {
  color: var(--danger-color, #ef4444);
}

.limit-hint {
  margin-bottom: var(--space-3);
}

.expired-tag {
  margin-left: 4px;
}

.history-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: var(--space-3);
  font-size: var(--font-xs);
}

.pending-alert {
  margin-bottom: var(--space-3);
}

.err-line {
  font-size: 12px;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.warn-num {
  color: var(--el-color-warning);
  font-weight: 600;
}

.warn-text {
  color: var(--el-color-danger);
}

.mono {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
}

.w-full {
  width: 100%;
}

.cron-row {
  display: flex;
  gap: var(--space-2);
  width: 100%;
}

.cron-preset {
  width: 180px;
  flex: none;
}

.cron-hint {
  font-size: 12px;
  margin-top: 2px;
}

.period-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
}

.period-preset {
  width: 180px;
  flex: none;
}

.period-kind {
  font-size: 12px;
}

.form-hint {
  font-size: 12px;
}
</style>