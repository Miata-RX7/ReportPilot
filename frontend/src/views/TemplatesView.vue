<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { MoreFilled } from '@element-plus/icons-vue'
import { ElMessage, type UploadFile, type UploadFiles } from 'element-plus'

import { confirmDelete } from '@/components/ConfirmDialog'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import InsightConfigDialog from '@/components/InsightConfigDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import OnlyofficeEditorDialog from '@/components/OnlyofficeEditorDialog.vue'
import TemplateGrantsDialog from '@/components/TemplateGrantsDialog.vue'
import TemplatePreviewDialog from '@/components/TemplatePreviewDialog.vue'
import TemplateVersionsDialog from '@/components/TemplateVersionsDialog.vue'
import { metricApi } from '@/api/metrics'
import { templateApi } from '@/api/templates'
import type {
  BindingItem,
  InsightAIConfig,
  MetricDefinitionRead,
  TemplateDetail,
  TemplateListItem,
} from '@/api/types'
import { parseApiError } from '@/utils/error'
import {
  MIXED_PLACEHOLDER_HINT,
  bindingProgressClass,
  bindingProgressText,
  canBindChartMetric,
  canEdit,
  canManage,
  permissionClass,
  permissionText,
} from '@/utils/template'

const rows = ref<TemplateListItem[]>([])
const metrics = ref<MetricDefinitionRead[]>([])
const loading = ref(false)

/* ---- 上传 ---- */
const uploadVisible = ref(false)
const uploadSubmitting = ref(false)
const uploadName = ref('')
const uploadFile = ref<File | null>(null)
const uploadFileList = ref<any[]>([])
const uploadResult = ref<TemplateDetail | null>(null)
const uploadViolations = ref<string[]>([])
const uploadError = ref<string | null>(null)

/* ---- 配置绑定 ---- */
const bindVisible = ref(false)
const bindSubmitting = ref(false)
const bindId = ref<number | null>(null)
const bindName = ref('')
const bindFilter = ref<'all' | 'bound' | 'unbound'>('all')
interface BindRow {
  placeholder: string
  type: 'metric' | 'insight'
  refId: number | null
  /** 变量是否独占段落（决定能否绑定图表类型指标） */
  alone: boolean
  /** 仅 AI 摘要：该段的提示词 / 参考范文 / 字数 / 温度 */
  aiConfig: InsightAIConfig
}
const bindRows = ref<BindRow[]>([])

/* ---- AI 摘要配置（提示词 + 参考范文 + 试跑） ---- */
const aiCfgVisible = ref(false)
const aiCfgRow = ref<BindRow | null>(null)

function openAiConfig(row: BindRow) {
  aiCfgRow.value = row
  aiCfgVisible.value = true
}

/** 弹窗「确定」：先只回填到表格，仍需点「保存绑定」才落库（避免误改线上模板） */
function onAiConfigSave(config: InsightAIConfig) {
  if (aiCfgRow.value) {
    aiCfgRow.value.aiConfig = config
    ElMessage.success('已应用，点击「保存绑定」后生效')
  }
}

/* ---- 预览 / 编辑 / 版本 / 授权 ---- */
const previewVisible = ref(false)
const previewRow = ref<TemplateListItem | null>(null)
const editorVisible = ref(false)
const editorRow = ref<TemplateListItem | null>(null)
const versionsVisible = ref(false)
const versionsRow = ref<TemplateListItem | null>(null)
const grantsVisible = ref(false)
const grantsRow = ref<TemplateListItem | null>(null)

/** 下拉选项文案：图表类型标注类型，并对不可绑定的目标给出原因 */
function metricOptionLabel(
  m: { name: string; metric_type?: string },
  alone: boolean,
): string {
  const typeText = { curve: '曲线图', pie: '饼图', scalar: '数值', text: '文本' }[
    m.metric_type ?? 'scalar'
  ] ?? m.metric_type
  if (!canBindChartMetric(m.metric_type, alone)) {
    return `${m.name}（${typeText}·需独占段落，此处不可绑定）`
  }
  return `${m.name}（${typeText}）`
}

/** 在模板中展示 {{ var }}（避免在 mustache 里写字面量 } 被编译器截断） */
function wrapVar(name: string): string {
  return '{{ ' + name + ' }}'
}

const bindUnbound = computed(() =>
  bindRows.value.filter((r) => r.type === 'metric' && r.refId === null).map((r) => r.placeholder),
)

/** 图表类型指标绑定到了非独占占位符（后端也会 422，这里提前拦一次） */
const bindIllegalCharts = computed(() =>
  bindRows.value
    .filter((r) => {
      if (r.type !== 'metric' || r.refId === null) return false
      const m = metrics.value.find((x) => x.id === r.refId)
      return !!m && !canBindChartMetric(m.metric_type, r.alone)
    })
    .map((r) => {
      const m = metrics.value.find((x) => x.id === r.refId)
      return `${wrapVar(r.placeholder)} 不能绑定${m?.metric_type === 'pie' ? '饼图' : '曲线图'}指标 ${m?.name ?? ''}`.trim()
    }),
)

/** 某行当前的图表绑定是否非法（用于行内标红） */
function rowChartError(row: BindRow): string | null {
  if (row.type !== 'metric' || row.refId === null) return null
  const m = metrics.value.find((x) => x.id === row.refId)
  if (!m || canBindChartMetric(m.metric_type, row.alone)) return null
  return `${MIXED_PLACEHOLDER_HINT}，请让该变量在 Word 中独占一段`
}
const filteredBindRows = computed(() => {
  if (bindFilter.value === 'all') return bindRows.value
  const isBound = (r: { type: string; refId: number | null }) => r.type === 'insight' || r.refId !== null
  return bindRows.value.filter((r) => (bindFilter.value === 'bound' ? isBound(r) : !isBound(r)))
})

async function load() {
  loading.value = true
  try {
    const [t, m] = await Promise.all([templateApi.list(), metricApi.list()])
    rows.value = t.data
    metrics.value = m.data.filter((x) => x.is_active)
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    loading.value = false
  }
}

/* ---- 上传 ---- */
function openUpload() {
  uploadName.value = ''
  uploadFile.value = null
  uploadFileList.value = []
  uploadResult.value = null
  uploadViolations.value = []
  uploadError.value = null
  uploadVisible.value = true
}

function onFileChange(file: UploadFile, files: UploadFiles) {
  uploadFileList.value = files
  const raw = file.raw
  if (!raw) return
  if (!raw.name.toLowerCase().endsWith('.docx')) {
    ElMessage.error('仅支持 .docx 文件')
    uploadFileList.value = files.filter((f) => f.uid !== file.uid)
    uploadFile.value = null
    return
  }
  if (raw.size > 10 * 1024 * 1024) {
    ElMessage.error('文件超过 10MB 限制')
    uploadFileList.value = files.filter((f) => f.uid !== file.uid)
    uploadFile.value = null
    return
  }
  uploadFile.value = raw
  uploadViolations.value = []
  uploadError.value = null
}

async function doUpload() {
  if (!uploadName.value.trim()) {
    ElMessage.warning('请输入模板名称')
    return
  }
  if (!uploadFile.value) {
    ElMessage.warning('请选择 .docx 模板文件')
    return
  }
  uploadSubmitting.value = true
  uploadViolations.value = []
  uploadError.value = null
  try {
    const { data } = await templateApi.upload(uploadName.value.trim(), uploadFile.value)
    uploadResult.value = data
    uploadFile.value = null
    uploadFileList.value = []
    ElMessage.success('模板已上传')
    await load()
  } catch (error) {
    const parsed = parseApiError(error)
    uploadError.value = parsed.message
    uploadViolations.value = parsed.violations ?? []
  } finally {
    uploadSubmitting.value = false
  }
}

/* ---- 配置绑定 ---- */
async function openBinding(row: TemplateListItem) {
  try {
    const { data } = await templateApi.detail(row.id)
    bindId.value = row.id
    bindName.value = row.name
    bindFilter.value = 'all'
    const byPh = new Map(data.bindings.map((b) => [b.placeholder, b]))
    bindRows.value = data.placeholders.map((p) => {
      const alone = p.is_alone_in_paragraph
      const b = byPh.get(p.name)
      const base = { placeholder: p.name, alone, aiConfig: {} as InsightAIConfig }
      if (b && b.binding_type === 'insight')
        return { ...base, type: 'insight' as const, refId: null, aiConfig: b.ai_config ?? {} }
      if (b && b.binding_type === 'metric')
        return { ...base, type: 'metric' as const, refId: b.binding_ref_id }
      return { ...base, type: 'metric' as const, refId: null }
    })
    bindVisible.value = true
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}

async function saveBindings() {
  if (bindUnbound.value.length > 0 || bindIllegalCharts.value.length > 0 || bindId.value === null) {
    if (bindIllegalCharts.value.length > 0) {
      ElMessage.warning('图表类型指标必须绑定到独占一段的变量')
    }
    return
  }
  bindSubmitting.value = true
  try {
    const bindings: BindingItem[] = bindRows.value.map((r) =>
      r.type === 'metric'
        ? { placeholder: r.placeholder, binding_type: 'metric', binding_ref_id: r.refId }
        : { placeholder: r.placeholder, binding_type: 'insight', ai_config: r.aiConfig },
    )
    await templateApi.putBindings(bindId.value, bindings)
    ElMessage.success('绑定已保存')
    bindVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    bindSubmitting.value = false
  }
}

/* ---- 预览 / 编辑 / 版本 / 授权 ---- */
function openPreview(row: TemplateListItem) {
  previewRow.value = row
  previewVisible.value = true
}

function editFromPreview() {
  previewVisible.value = false
  if (!previewRow.value) return
  editorRow.value = previewRow.value
  editorVisible.value = true
}

function openEditor(row: TemplateListItem) {
  editorRow.value = row
  editorVisible.value = true
}

async function onEditorSaved() {
  await load()
  if (previewRow.value) {
    const refreshed = rows.value.find((r) => r.id === previewRow.value?.id)
    if (refreshed) previewRow.value = refreshed
  }
}

function openVersions(row: TemplateListItem) {
  versionsRow.value = row
  versionsVisible.value = true
}

function openGrants(row: TemplateListItem) {
  grantsRow.value = row
  grantsVisible.value = true
}

/* ---- 删除 ---- */
/** 三点菜单：版本 / 授权 / 删除 */
function onMoreAction(cmd: string, row: TemplateListItem) {
  if (cmd === 'versions') {
    openVersions(row)
  } else if (cmd === 'grants') {
    openGrants(row)
  } else if (cmd === 'delete') {
    void onDelete(row)
  }
}

async function onDelete(row: TemplateListItem) {
  const ok = await confirmDelete(row.name)
  if (!ok) return
  try {
    await templateApi.remove(row.id)
    ElMessage.success('模板已删除')
    await load()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}

onMounted(load)
</script>

<template>
  <div class="page-container">
    <PageHeader title="模板管理" description="上传 Word 模板、维护版本、绑定指标/AI 摘要并在线编辑">
      <template #actions>
        <el-button type="primary" @click="openUpload">上传模板</el-button>
      </template>
    </PageHeader>

    <DataTable :data="rows" :loading="loading" empty-text="暂无模板，点击右上角上传">
      <template #columns>
        <el-table-column prop="name" label="模板名" min-width="160" />
        <el-table-column label="属主" width="120">
          <template #default="{ row }">{{ row.owner_name || '—' }}</template>
        </el-table-column>
        <el-table-column label="当前版本" width="100">
          <template #default="{ row }">
            <span class="code-chip">{{ row.current_version_no ? `v${row.current_version_no}` : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="变量数" width="90">
          <template #default="{ row }">
            <span class="code-chip">{{ row.placeholder_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="绑定进度" width="140">
          <template #default="{ row }">
            <span
              :class="[
                'soft-tag',
                `soft-tag--${bindingProgressClass({ total: row.placeholder_count, bound: row.bound_count, unbound: [] })}`,
              ]"
            >
              {{ bindingProgressText({ total: row.placeholder_count, bound: row.bound_count, unbound: [] }) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="我的权限" width="100">
          <template #default="{ row }">
            <span :class="['soft-tag', `soft-tag--${permissionClass(row.permission)}`]">
              {{ permissionText(row.permission) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="250" fixed="right">
          <template #default="{ row }">
            <div class="action-cell">
              <el-button link type="primary" data-testid="tpl-action-preview" @click="openPreview(row)">预览</el-button>
              <el-button
                v-if="canEdit(row.permission)"
                link
                type="primary"
                data-testid="tpl-action-edit"
                @click="openEditor(row)"
              >
                在线编辑
              </el-button>
              <el-button
                v-if="canEdit(row.permission)"
                link
                type="primary"
                data-testid="tpl-action-bind"
                @click="openBinding(row)"
              >
                配置绑定
              </el-button>
              <!-- 版本 / 授权 / 删除 收进三点菜单，避免操作列过宽 -->
              <el-dropdown trigger="click" @command="(cmd: string) => onMoreAction(cmd, row)">
                <el-button link type="primary" data-testid="tpl-action-more">
                  <el-icon><MoreFilled /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item
                      v-if="canEdit(row.permission)"
                      command="versions"
                      data-testid="tpl-more-versions"
                    >
                      版本
                    </el-dropdown-item>
                    <el-dropdown-item
                      v-if="canManage(row.permission)"
                      command="grants"
                      data-testid="tpl-more-grants"
                    >
                      授权
                    </el-dropdown-item>
                    <el-dropdown-item
                      v-if="canManage(row.permission)"
                      command="delete"
                      divided
                      class="danger-item"
                      data-testid="tpl-more-delete"
                    >
                      删除
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </el-table-column>
      </template>
    </DataTable>

    <!-- 上传弹窗 -->
    <FormDialog
      v-model:visible="uploadVisible"
      title="上传模板"
      :submitting="uploadSubmitting"
      @submit="doUpload"
    >
      <el-form label-width="80px" @submit.prevent>
        <el-form-item label="名称" required>
          <el-input v-model="uploadName" placeholder="如 运维月报模板" />
        </el-form-item>
        <el-form-item label="文件" required>
          <el-upload
            :auto-upload="false"
            :limit="1"
            accept=".docx"
            :file-list="uploadFileList"
            :on-change="onFileChange"
            :on-remove="() => (uploadFile = null)"
          >
            <el-button>选择文件</el-button>
            <template #tip>
              <div class="text-muted upload-tip">仅支持 .docx，大小 ≤ 10MB</div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="uploadError"
        :title="uploadError"
        type="error"
        show-icon
        :closable="false"
        class="upload-alert"
      >
        <template v-if="uploadViolations.length">
          <div v-for="(v, i) in uploadViolations" :key="i" class="violation">{{ v }}</div>
        </template>
      </el-alert>

      <template v-if="uploadResult">
        <el-divider />
        <div class="parsed-title">已解析变量（{{ uploadResult.placeholders.length }}）</div>
        <div class="parsed-list">
          <span
            v-for="p in uploadResult.placeholders"
            :key="p.name"
            class="code-chip parsed-chip"
          >{{ wrapVar(p.name) }}</span>
        </div>
      </template>
    </FormDialog>

    <!-- 配置绑定抽屉 -->
    <el-dialog
      v-model="bindVisible"
      class="bind-dialog"
      :title="`配置绑定 - ${bindName}`"
      width="min(980px, 92vw)"
      top="6vh"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-alert
        type="info"
        show-icon
        :closable="false"
        title="曲线图 / 饼图指标以图片插入 Word，要求变量在模板中独占一段（不能与其它文字混排）"
        class="bind-hint"
        data-testid="bind-chart-hint"
      />
      <el-tabs v-model="bindFilter">
        <el-tab-pane label="全部" name="all" />
        <el-tab-pane label="已绑定" name="bound" />
        <el-tab-pane label="未绑定" name="unbound" />
      </el-tabs>

      <el-table :data="filteredBindRows">
        <el-table-column label="Word 占位符" min-width="180">
          <template #default="{ row }">
            <span class="code-chip">{{ wrapVar(row.placeholder) }}</span>
            <el-tooltip v-if="!row.alone" :content="MIXED_PLACEHOLDER_HINT" placement="top">
              <span class="soft-tag soft-tag--warning mix-tag" data-testid="ph-mixed-tag">混排</span>
            </el-tooltip>
            <el-tooltip v-else content="独占一段，可绑定图表类型指标" placement="top">
              <span class="soft-tag soft-tag--success mix-tag" data-testid="ph-alone-tag">独占</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="190">
          <template #default="{ row }">
            <el-radio-group v-model="row.type">
              <el-radio label="metric">指标</el-radio>
              <el-radio label="insight">AI 摘要</el-radio>
            </el-radio-group>
          </template>
        </el-table-column>
        <el-table-column label="绑定目标" min-width="220">
          <template #default="{ row }">
            <el-select
              v-if="row.type === 'metric'"
              v-model="row.refId"
              placeholder="选择指标"
              filterable
              class="full-width"
            >
              <el-option
                v-for="m in metrics"
                :key="m.id"
                :label="metricOptionLabel(m, row.alone)"
                :value="m.id"
                :disabled="!canBindChartMetric(m.metric_type, row.alone)"
              />
            </el-select>
            <template v-else>
              <el-button
                size="small"
                plain
                data-testid="insight-cfg-open"
                @click="openAiConfig(row)"
              >
                {{
                  row.aiConfig.prompt || row.aiConfig.sample_text
                    ? '编辑提示词 / 范文'
                    : '配置提示词 / 范文'
                }}
              </el-button>
              <span v-if="row.aiConfig.sample_text" class="soft-tag soft-tag--info mix-tag">
                含范文
              </span>
            </template>
            <div v-if="rowChartError(row)" class="row-error" data-testid="bind-row-error">
              {{ rowChartError(row) }}
            </div>
          </template>
        </el-table-column>
      </el-table>

      <el-alert
        v-if="bindIllegalCharts.length"
        type="error"
        show-icon
        :closable="false"
        class="bind-alert"
        data-testid="bind-illegal-charts"
        :title="`图表类型指标必须绑定到独占一段的变量：${bindIllegalCharts.join('；')}`"
      />
      <el-alert
        v-if="bindUnbound.length"
        type="warning"
        show-icon
        :closable="false"
        class="bind-alert"
        :title="`以下变量未完成绑定：${bindUnbound.join('、')}`"
      />

      <template #footer>
        <el-button @click="bindVisible = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="bindUnbound.length > 0 || bindIllegalCharts.length > 0"
          :loading="bindSubmitting"
          data-testid="bind-save"
          @click="saveBindings"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- AI 摘要：提示词 / 参考范文 / 试跑（确定后仍需「保存绑定」才落库） -->
    <InsightConfigDialog
      v-if="aiCfgRow"
      v-model="aiCfgVisible"
      :template-id="bindId ?? 0"
      :placeholder="aiCfgRow.placeholder"
      :config="aiCfgRow.aiConfig"
      @save="onAiConfigSave"
    />

    <!-- 预览弹窗（原始文档 + 在线编辑入口） -->
    <TemplatePreviewDialog
      v-if="previewRow"
      v-model="previewVisible"
      :template-id="previewRow.id"
      :template-name="previewRow.name"
      :permission="previewRow.permission"
      @edit="editFromPreview"
    />

    <!-- ONLYOFFICE 在线编辑弹窗 -->
    <OnlyofficeEditorDialog
      v-if="editorRow"
      v-model="editorVisible"
      :template-id="editorRow.id"
      :template-name="editorRow.name"
      @saved="onEditorSaved"
    />

    <!-- 版本历史 -->
    <TemplateVersionsDialog
      v-if="versionsRow"
      v-model="versionsVisible"
      :template-id="versionsRow.id"
      :template-name="versionsRow.name"
      :permission="versionsRow.permission"
      @activated="load"
    />

    <!-- 授权管理 -->
    <TemplateGrantsDialog
      v-if="grantsRow"
      v-model="grantsVisible"
      :template-id="grantsRow.id"
      :template-name="grantsRow.name"
      :permission="grantsRow.permission"
      :owner-id="grantsRow.owner_id"
      @changed="load"
    />
  </div>
</template>

<style scoped lang="scss">
.upload-tip {
  font-size: var(--font-xs);
}

.upload-alert {
  margin-top: var(--space-3);
}

.violation {
  font-family: var(--font-mono);
  font-size: var(--font-xs);
  margin-top: var(--space-1);
}

.parsed-title {
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
  margin-bottom: var(--space-2);
}

.parsed-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.parsed-chip {
  margin: 0;
}

.bind-hint {
  margin-bottom: var(--space-3);
}

.action-cell {
  display: flex;
  align-items: center;
  gap: var(--space-3);

  .el-button {
    margin-left: 0;
  }
}

:deep(.danger-item) {
  color: var(--danger-color, #ef4444);
}

.mix-tag {
  margin-left: var(--space-1);
  cursor: help;
}

.row-error {
  margin-top: var(--space-1);
  font-size: 12px;
  color: var(--danger-color, #ef4444);
  line-height: 1.4;
}

.bind-alert {
  margin-top: var(--space-4);
}

.full-width {
  width: 100%;
}
</style>
