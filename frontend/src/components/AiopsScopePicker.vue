<script setup lang="ts">
/**
 * AIOps 取数范围级联：**模型 → 实例 → 监控项 →（列 → 主键行）**。
 *
 * 与 Zabbix 的三级联并存（用户决定「两套代码并行维护」），互不影响：
 * 本组件只服务 `data_source.type === 'aiops'`，不碰任何 Zabbix 逻辑。
 *
 * 三条来自 2026-10-16 实测的关键设计（改动前先读 backend/app/services/aiops_keys.py）：
 * 1. **列排在主键行前面** —— 行列表要按「该列当前值非空」过滤（实测当前值非空⟺有历史），
 *    所以换列必须换行集，顺序反了就会出现「选了行却没数据」
 * 2. list 监控项的列元数据在 `item/list` 里已全部返回，**不需要额外请求**
 * 3. 模型列表已由后端过滤掉「未开监控」与「0 实例」的，前端不再重复判断
 */
import { computed, ref, watch } from 'vue'

import { dataSourceApi } from '@/api/datasources'
import type { HostGroupRef, HostRef, ItemRef, ItemRowRef } from '@/api/types'
import {
  type AiopsColumn,
  type AiopsPick,
  type MetricType,
  buildPicks,
  columnLabel,
  picksToSelections,
} from '@/utils/aiopsRef'

/** 曲线配置的三级级联节点（el-cascader lazy 的数据结构）。 */
interface CascaderNode {
  value: string
  raw: string
  label: string
  /** 第 1 级用来标注「数值 / 表格」 */
  tag?: string
  /** 第 3 级行对应的当前值，右侧灰字展示 */
  hint?: string
  /** 表格项不可直接选中，必须往下选列与行 */
  selectable?: boolean
}

const props = defineProps<{
  datasourceId: number | null
  /** 已选模型（= 主机组语义） */
  modelIds: string[]
  /** 已选实例（= 主机语义） */
  instanceIds: string[]
  /** 已选取数对象（存的是定位串） */
  picks: AiopsPick[]
  /**
   * 当前指标类型。决定监控项区展示什么：
   * - scalar / pie：下拉多选（数值项 + 已展开的列表列/行）
   * - text：下拉多选（文本项 + 列表的 text 列）
   * - curve：**不展示**取数对象选择，改为把候选交给父组件的「曲线配置」
   */
  metricType: MetricType
}>()

const emit = defineEmits<{
  'update:modelIds': [value: string[]]
  'update:instanceIds': [value: string[]]
  'update:picks': [value: AiopsPick[]]
  /** 范围（模型/实例）变化 —— 父级据此清空已选取数对象 */
  'scope-change': []
  /** 当前实例下所有可选的取数对象（曲线的「曲线配置」用它渲染下拉） */
  'update:candidates': [value: AiopsPick[]]
}>()

const modelOptions = ref<HostGroupRef[]>([])
const instanceOptions = ref<HostRef[]>([])
const itemOptions = ref<ItemRef[]>([])
const rowOptions = ref<ItemRowRef[]>([])

const loadingModels = ref(false)
const loadingInstances = ref(false)
const loadingItems = ref(false)
const loadingRows = ref(false)
const loadError = ref('')

/** 非 list 监控项的勾选（监控项 id） */
const plainItemIds = ref<string[]>([])
/** list 监控项的选择：{ [itemId]: { columnId, rowKeys } } */
const listSelections = ref<Record<string, { columnId: string; rowKeys: string[] }>>({})

const instances = computed(() => instanceOptions.value)
const selectedInstanceId = computed(() => props.instanceIds[0] ?? '')

/** 单实例使用（AIOps 一个指标限定在一个实例内，避免跨实例语义混淆）。 */
function emitScope(modelIds: string[], instanceIds: string[]) {
  emit('update:modelIds', modelIds)
  emit('update:instanceIds', instanceIds)
}

/** 上次 emit 出去的 picks 签名。用于打断「emit -> prop 变化 -> watch 又跑」的死循环。 */
let lastPublished = ''

function publish() {
  const next = buildCurrentPicks()
  const sig = next.map((p) => p.ref).join('|')
  if (sig === lastPublished) return
  lastPublished = sig
  emit('update:picks', next)
}

function buildCurrentPicks(): AiopsPick[] {
  if (!selectedInstanceId.value) return []
  // 监控项还没加载出来时**不要**把已回填的 picks 清空：实例可能压根没有监控项，
  // 此时 publish 会算出空数组，等于把编辑进来的配置抹掉。
  if (!itemOptions.value.length) return props.picks
  const items = itemOptions.value
    .filter((i) => plainItemIds.value.includes(i.item_id) || listSelections.value[i.item_id])
    .map((i) => ({
      id: i.item_id,
      name: i.name,
      dataType: (i.aiops_data_type ?? 'number') as 'number' | 'text' | 'list',
      columns: i.aiops_columns,
    }))
  return buildPicks(selectedInstanceId.value, items, listSelections.value)
}

/* ------------------------------------------------------------ 数据加载 */

async function loadModels() {
  if (props.datasourceId === null) return
  loadingModels.value = true
  loadError.value = ''
  try {
    modelOptions.value = (await dataSourceApi.listHostGroups(props.datasourceId)).data
  } catch {
    loadError.value = '模型列表加载失败，请检查数据源连通性'
  } finally {
    loadingModels.value = false
  }
}

async function loadInstances(modelId: string, keyword?: string) {
  if (props.datasourceId === null || !modelId) return
  loadingInstances.value = true
  try {
    const { data } = await dataSourceApi.listHosts(props.datasourceId, [modelId], keyword)
    instanceOptions.value = data
  } catch {
    loadError.value = '实例列表加载失败'
  } finally {
    loadingInstances.value = false
  }
}

async function loadItems(instanceId: string) {
  if (props.datasourceId === null || !instanceId) return
  loadingItems.value = true
  try {
    const { data } = await dataSourceApi.listItems(props.datasourceId, [instanceId])
    // 在入口统一过滤 log_* 并排序：下游所有取数路径（级联、文本下拉）都受益，
    // 不会出现「级联里没有、文本下拉里却有」的割裂
    itemOptions.value = sortItems(data.filter((i) => !isHiddenItem(i)))
    if (!data.length) loadError.value = '该实例没有任何监控项，请换一个实例'
    else if (loadError.value.startsWith('该实例')) loadError.value = ''
  } catch {
    loadError.value = '监控项列表加载失败'
  } finally {
    loadingItems.value = false
  }
}

async function loadRows(itemId: string, columnId: string) {
  if (props.datasourceId === null || !selectedInstanceId.value || !columnId) {
    rowOptions.value = []
    return
  }
  loadingRows.value = true
  try {
    const { data } = await dataSourceApi.listItemRows(
      props.datasourceId,
      selectedInstanceId.value,
      itemId,
      columnId,
    )
    rowOptions.value = data
  } catch {
    rowOptions.value = []
    loadError.value = '主键行加载失败'
  } finally {
    loadingRows.value = false
  }
}

/* ------------------------------------------------------------ 级联联动 */

function onModelsChange(modelIds: string[]) {
  emitScope(modelIds, [])
  instanceOptions.value = []
  itemOptions.value = []
  resetItems()
  emit('scope-change')
  const first = modelIds[0]
  if (first) void loadInstances(first)
}

function onInstanceChange(instanceId: string) {
  emitScope(props.modelIds, instanceId ? [instanceId] : [])
  itemOptions.value = []
  resetItems()
  emit('scope-change')
  if (instanceId) void loadItems(instanceId)
}

function resetItems() {
  plainItemIds.value = []
  listSelections.value = {}
  rowOptions.value = []
  lastPublished = '' // 换范围后签名要重新计，否则清空动作会被去重逻辑吞掉
  publish()
}

function onPlainItemsChange(ids: string[]) {
  plainItemIds.value = ids
  publish()
}

/** 选列：必须清掉旧行 —— 不同列的有效行不同（实测 26 行里只有 7 行有数据） */
function onColumnChange(itemId: string, columnId: string) {
  listSelections.value = {
    ...listSelections.value,
    [itemId]: { columnId, rowKeys: [] },
  }
  rowOptions.value = []
  void loadRows(itemId, columnId)
  publish()
}

function onRowsChange(itemId: string, rowKeys: string[]) {
  const cur = listSelections.value[itemId]
  if (!cur) return
  listSelections.value = { ...listSelections.value, [itemId]: { ...cur, rowKeys } }
  publish()
}

/** 取消勾选某个 list 监控项 */
function onListItemUncheck(itemId: string) {
  const next = { ...listSelections.value }
  delete next[itemId]
  listSelections.value = next
  publish()
}

const listItems = computed(() => itemOptions.value.filter((i) => i.aiops_data_type === 'list'))

/**
 * 简单监控项（number / text）在当前指标类型下是否允许出现在下拉里。
 *
 * 依据 utils/aiopsRef.ts 的判定表：text 型监控项只能做文本指标，
 * 所以**数值 / 曲线 / 饼图指标里不出现**（用户 2026-10-17 明确要求）。
 */
function plainItemAllowed(item: ItemRef): boolean {
  return props.metricType === 'text' ? item.aiops_data_type === 'text' : item.aiops_data_type === 'number'
}

/** 下拉里可勾的简单监控项 */
const plainItems = computed(() => itemOptions.value.filter(plainItemAllowed))

/**
 * 当前实例下所有可选取数对象（父组件的曲线配置拿它渲染下拉）。
 *
 * = 简单监控项（按类型过滤） + **已展开**的列表列/行组合。
 * 列表项必须先在下面选列与行才会进来：一个端口表有 26 行 × 14 列，
 * 全量拉回来既慢又没必要（实测 rows 要逐列单独请求）。
 */
const candidates = computed<AiopsPick[]>(() => {
  const inst = selectedInstanceId.value
  if (!inst) return []
  const base = buildPicks(
    inst,
    itemOptions.value
      .filter((i) => plainItemAllowed(i) || listSelections.value[i.item_id])
      .map((i) => ({
        id: i.item_id,
        name: i.name,
        dataType: (i.aiops_data_type ?? 'number') as 'number' | 'text' | 'list',
        columns: i.aiops_columns,
      })),
    listSelections.value,
  )
  return base
})

/* ------------------------------------------------- 曲线配置的三级级联数据 */

/**
 * 需要隐藏的监控项：``log_`` 开头。
 *
 * 这是 syslog 计数（log_warning / log_err / log_emerg …），数值本身是累计条数，
 * 当指标用既没业务含义又会误导（今天比昨天大只说明多收了几条日志）。
 * 用户 2026-10-17 要求直接屏蔽。
 */
function isHiddenItem(item: ItemRef): boolean {
  return /^log_/i.test(item.name ?? '') || /^log_/i.test(item.key ?? '')
}

/** 排序：数值在上、表格在下，各自按名称排（用户 2026-10-17 要求）。 */
function sortItems(items: ItemRef[]): ItemRef[] {
  return [...items].sort((a, b) => {
    const aTable = a.aiops_data_type === 'list' ? 1 : 0
    const bTable = b.aiops_data_type === 'list' ? 1 : 0
    if (aTable !== bTable) return aTable - bTable
    return (a.name ?? '').localeCompare(b.name ?? '', 'zh-Hans-CN')
  })
}

/** 第 1 级：监控项（数值 / 表格，标注类型）。文本类不出现 —— 它只能做文本指标。 */
function cascaderItems(): CascaderNode[] {
  return sortItems(itemOptions.value)
    .filter((i) => i.aiops_data_type !== 'text')
    .map((i) => ({
      value: i.item_id,
      raw: i.item_id,
      label: i.name,
      tag: i.aiops_data_type === 'list' ? '表格' : '数值',
      // 表格项必须继续往下选列与行；数值项本身就是取数对象，可直接选中
      selectable: i.aiops_data_type !== 'list',
    }))
}

/** 第 2 级：非主键列中「数值类型且有历史」的列（用户 2026-10-17 明确要求）。 */
function cascaderColumns(itemId: string): CascaderNode[] {
  const it = itemOptions.value.find((i) => i.item_id === itemId)
  return (it?.aiops_columns ?? [])
    .filter((c) => !c.is_primary && c.value_type === 'number' && c.has_history)
    .map((c) => ({
      value: String(c.column_id),
      raw: String(c.column_id),
      label: columnLabel(c),
    }))
}

/** 第 3 级：主键列每一行的具体值（后端已按「当前值非空即有历史」过滤）。 */
async function cascaderRows(itemId: string, columnId: string): Promise<CascaderNode[]> {
  const inst = selectedInstanceId.value
  if (!inst || props.datasourceId === null) return []
  try {
    const { data } = await dataSourceApi.listItemRows(
      props.datasourceId,
      inst,
      itemId,
      columnId,
    )
    return data.map((r) => ({
      value: r.row_key,
      raw: r.row_key,
      label: r.row_key,
      hint: r.current_value,
    }))
  } catch {
    return []
  }
}
watch(candidates, (v) => emit('update:candidates', v), { immediate: true, deep: true })

function columnsOf(item: ItemRef): AiopsColumn[] {
  return item.aiops_columns ?? []
}

function columnById(item: ItemRef, columnId: string): AiopsColumn | undefined {
  if (!columnId) return undefined
  return columnsOf(item).find((c) => String(c.column_id) === String(columnId))
}

function selectionOf(itemId: string): { columnId: string; rowKeys: string[] } {
  return listSelections.value[itemId] ?? { columnId: '', rowKeys: [] }
}

/* ------------------------------------------------------------ 回显 / 外部驱动 */

watch(
  () => props.datasourceId,
  async (id, oldId) => {
    // immediate 的那次执行 oldId 是 undefined，那是「挂载」而不是「换了数据源」。
    // 挂载时不能清空父组件已回填的 scope —— 编辑弹窗是先填好 model_ids /
    // instance_ids 再打开的，一清就等于把配置丢了。
    if (oldId !== undefined) {
      resetItems()
      emitScope([], [])
    }
    if (id !== null) {
      await loadModels()
    } else {
      modelOptions.value = []
    }
  },
  // immediate 是必需的：本组件由父组件的 v-if="isAiopsDs" 挂载，挂载那一刻
  // datasourceId **往往已经是选中的值**（弹窗打开时就带着上次选的数据源，
  // 或用户先选数据源才切到 AIOps 分支）。没有 immediate，watcher 只注册不触发，
  // loadModels() 永远不执行 -> 模型下拉空白 / "No data"。
  // 见 AGENTS.md §3.12 同类问题。
  { immediate: true },
)

// 外部（编辑回显）灌入已保存的 picks -> 还原勾选状态
watch(
  () => props.picks,
  async (picks) => {
    // 即使没有已选取数对象，也要先把实例/监控项拉起来 ——
    // 否则编辑时 el-select 找不到对应option，只能把原始 id 当标签显示。
    const modelId = props.modelIds[0]
    if (modelId && !instances.value.length) await loadInstances(modelId)
    const instId = props.instanceIds[0]
    if (instId && !itemOptions.value.length) await loadItems(instId)
    if (!picks.length) return
    const refs = picks.map((p) => p.ref)
    const restored = picksToSelections(refs, () => '')
    plainItemIds.value = restored.plainItemIds
    listSelections.value = restored.listSelections
    // 命中 list 项的列需要把主键行拉出来，否则回显只有列、没有行
    for (const [itemId, sel] of Object.entries(restored.listSelections)) {
      if (sel.columnId && sel.rowKeys.length) await loadRows(itemId, sel.columnId)
    }
    publish()
  },
  // 同上：编辑弹窗挂载时 picks 已经是从后端灌进来的，不会再"变化"，
  // 没有 immediate 就永远不触发回显逻辑。
  { deep: true, immediate: true },
)

defineExpose({ plainItemAllowed, cascaderItems, cascaderColumns, cascaderRows })
</script>

<template>
  <div class="aiops-picker">
    <el-alert
      v-if="loadError"
      :title="loadError"
      type="warning"
      show-icon
      :closable="false"
      class="picker-alert"
    />

    <el-form-item label="模型" required>
      <el-select
        :model-value="modelIds"
        multiple
        collapse-tags
        collapse-tags-tooltip
        filterable
        class="full-width"
        :loading="loadingModels"
        :disabled="datasourceId === null"
        placeholder="选择模型（仅列出已开启监控且有实例的）"
        data-testid="aiops-select-models"
        @update:model-value="onModelsChange"
      >
        <el-option
          v-for="m in modelOptions"
          :key="m.group_id"
          :label="m.name"
          :value="m.group_id"
        >
          <span class="opt-name">{{ m.name }}</span>
          <span v-if="m.instance_num" class="opt-meta">{{ m.instance_num }} 实例</span>
        </el-option>
      </el-select>
    </el-form-item>

    <el-form-item label="实例" required>
      <el-select
        :model-value="selectedInstanceId"
        :loading="loadingInstances"
        :disabled="modelIds.length === 0"
        class="full-width"
        filterable
        clearable
        placeholder="选择实例"
        data-testid="aiops-select-instances"
        @update:model-value="onInstanceChange"
        @visible-change="(v: boolean) => { if (v && modelIds[0]) void loadInstances(modelIds[0]) }"
      >
        <el-option
          v-for="h in instances"
          :key="h.host_id"
          :label="h.name"
          :value="h.host_id"
        >
          <span class="opt-name">{{ h.name }}</span>
          <span v-if="h.ip" class="opt-meta">{{ h.ip }}</span>
        </el-option>
      </el-select>
    </el-form-item>

    <!-- 数值 / 曲线：取数对象都用三级级联，由父组件渲染（用户 2026-10-17 要求一致） -->
    <template v-if="metricType === 'text'">
    <el-form-item label="监控项">
      <div v-loading="loadingItems" class="picker-block">
        <el-alert
          v-if="!loadingItems && selectedInstanceId && !itemOptions.length"
          title="该实例没有监控项，换一个实例试试"
          type="info"
          :closable="false"
        />
        <el-empty
          v-else-if="!selectedInstanceId"
          description="请先选择模型与实例"
          :image-size="60"
        />
        <template v-else>
          <!-- 简单监控项：下拉多选（已按指标类型过滤，数值指标里不出现文本项） -->
          <el-select
            v-if="plainItems.length"
            :model-value="plainItemIds"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            class="full-width"
            :placeholder="metricType === 'text' ? '选择文本类监控项' : '选择数值类监控项'"
            data-testid="aiops-select-items"
            @update:model-value="onPlainItemsChange"
          >
            <el-option
              v-for="it in plainItems"
              :key="it.item_id"
              :label="it.name"
              :value="it.item_id"
            >
              <span class="opt-name">{{ it.name }}</span>
              <el-tag
                size="small"
                effect="plain"
                :type="it.aiops_data_type === 'number' ? 'primary' : 'info'"
              >
                {{ it.aiops_data_type === 'number' ? '数值' : '文本' }}
              </el-tag>
              <span v-if="it.aiops_cycle" class="opt-meta">{{ it.aiops_cycle }}s</span>
            </el-option>
          </el-select>
          <div v-else-if="!listItems.length" class="form-hint">
            {{
              metricType === 'text'
                ? '该实例没有文本类监控项'
                : '该实例没有数值类监控项（文本类请切到「文本」指标类型）'
            }}
          </div>

          <!-- list型：先选列与主键行，产出的组合会进入上面的下拉候选 -->
          <div v-if="listItems.length" class="list-source">
            <div class="list-source__title">
              表格类监控项（需先选列与行，再在上方下拉里勾选）
            </div>
          <div v-for="it in listItems" :key="it.item_id" class="list-item">
            <el-checkbox
              :model-value="!!listSelections[it.item_id]"
              @change="(v: unknown) => { if (v) { if (!listSelections[it.item_id]) onColumnChange(it.item_id, '') } else onListItemUncheck(it.item_id) }"
            >
              {{ it.name }}
              <el-tag size="small" effect="plain" type="warning">表格</el-tag>
              <span class="opt-meta">{{ columnsOf(it).length }} 列</span>
            </el-checkbox>

            <div v-if="listSelections[it.item_id]" class="list-expanded">
              <el-form-item label="列" required>
                <el-select
                  :model-value="selectionOf(it.item_id).columnId"
                  class="full-width"
                  placeholder="先选列（列决定这列能配成什么指标）"
                  data-testid="aiops-select-column"
                  @update:model-value="(v: string) => onColumnChange(it.item_id, v)"
                >
                  <el-option
                    v-for="c in columnsOf(it)"
                    :key="c.column_id"
                    :label="columnLabel(c)"
                    :value="String(c.column_id)"
                  >
                    <span class="opt-name">{{ columnLabel(c) }}</span>
                    <el-tag
                      size="small"
                      effect="plain"
                      :type="c.value_type === 'number' ? 'primary' : 'info'"
                    >
                      {{ c.value_type === 'number' ? '数值' : '文本' }}
                    </el-tag>
                    <el-tag v-if="!c.has_history" size="small" type="info" effect="plain">无历史</el-tag>
                  </el-option>
                </el-select>
                <div v-if="selectionOf(it.item_id).columnId" class="form-hint">
                  <template v-if="!columnById(it, selectionOf(it.item_id).columnId)?.has_history">
                    该列没有历史数据，只有当前值 —— 只能配「数值」，配曲线会是空图
                  </template>
                </div>
              </el-form-item>

              <el-form-item v-if="selectionOf(it.item_id).columnId" label="主键行" required>
                <el-select
                  :model-value="selectionOf(it.item_id).rowKeys"
                  multiple
                  filterable
                  collapse-tags
                  collapse-tags-tooltip
                  class="full-width"
                  :loading="loadingRows"
                  :placeholder="rowOptions.length ? '选择要取数的行' : '该列暂无数据行'"
                  data-testid="aiops-select-rows"
                  @update:model-value="(v: string[]) => onRowsChange(it.item_id, v)"
                >
                  <el-option
                    v-for="r in rowOptions"
                    :key="r.row_key"
                    :label="r.row_key"
                    :value="r.row_key"
                  >
                    <span class="opt-name">{{ r.row_key }}</span>
                    <span class="opt-meta">{{ r.current_value }}</span>
                  </el-option>
                </el-select>
                <div class="form-hint">
                  已过滤掉该列当前值为空的行（实测当前值为空即没有历史数据）
                </div>
              </el-form-item>
            </div>
          </div>
          </div>
        </template>
      </div>
    </el-form-item>
    </template>
    <div v-else class="form-hint curve-hint">
      {{ metricType === 'curve' ? '曲线图' : '数值' }}类型请在下方用三级级联选择「监控项 / 列 / 行」
    </div>
  </div>
</template>

<style scoped lang="scss">
.picker-block {
  width: 100%;
  min-height: 40px;
}
.picker-alert {
  margin-bottom: var(--space-3);
}
.full-width {
  width: 100%;
}
.item-group {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
}
.item-check {
  margin-right: 0;
}
.list-source {
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}
.list-source__title {
  margin-bottom: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--font-xs);
}
.curve-hint {
  margin-top: var(--space-2);
}
.list-item {
  margin-top: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-border);
}
.list-expanded {
  margin-top: var(--space-2);
  padding-left: var(--space-4);
  border-left: 2px solid var(--color-border);
}
.opt-name {
  margin-right: var(--space-2);
}
.opt-meta {
  float: right;
  color: var(--color-text-tertiary);
  font-size: var(--font-xs);
}
</style>
