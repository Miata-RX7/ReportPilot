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
  allowedMetricTypes,
  buildPicks,
  columnLabel,
  picksToSelections,
} from '@/utils/aiopsRef'

const props = defineProps<{
  datasourceId: number | null
  /** 已选模型（= 主机组语义） */
  modelIds: string[]
  /** 已选实例（= 主机语义） */
  instanceIds: string[]
  /** 已选取数对象（存的是定位串） */
  picks: AiopsPick[]
}>()

const emit = defineEmits<{
  'update:modelIds': [value: string[]]
  'update:instanceIds': [value: string[]]
  'update:picks': [value: AiopsPick[]]
  /** 范围（模型/实例）变化 —— 父级据此清空已选取数对象 */
  'scope-change': []
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

function publish() {
  if (!selectedInstanceId.value) {
    emit('update:picks', [])
    return
  }
  const items = itemOptions.value
    .filter((i) => plainItemIds.value.includes(i.item_id) || listSelections.value[i.item_id])
    .map((i) => ({
      id: i.item_id,
      name: i.name,
      dataType: (i.aiops_data_type ?? 'number') as 'number' | 'text' | 'list',
      columns: i.aiops_columns,
    }))
  emit(
    'update:picks',
    buildPicks(selectedInstanceId.value, items, listSelections.value),
  )
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
    itemOptions.value = data
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
  async (id) => {
    resetItems()
    emitScope([], [])
    if (id !== null) {
      await loadModels()
    } else {
      modelOptions.value = []
    }
  },
)

// 外部（编辑回显）灌入已保存的 picks -> 还原勾选状态
watch(
  () => props.picks,
  async (picks) => {
    if (!picks.length) return
    const refs = picks.map((p) => p.ref)
    const restored = picksToSelections(refs, () => '')
    plainItemIds.value = restored.plainItemIds
    listSelections.value = restored.listSelections
    // 回显时补齐尚未加载的下游数据
    const modelId = props.modelIds[0]
    if (modelId && !instances.value.length) await loadInstances(modelId)
    const instId = props.instanceIds[0]
    if (instId && !itemOptions.value.length) await loadItems(instId)
    // 命中 list 项的列需要把主键行拉出来，否则回显只有列、没有行
    for (const [itemId, sel] of Object.entries(restored.listSelections)) {
      if (sel.columnId && sel.rowKeys.length) await loadRows(itemId, sel.columnId)
    }
    publish()
  },
  { deep: true },
)

defineExpose({ allowedFor: allowedMetricTypes })
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
        :model-value="instanceIds"
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
          <!-- 非 list：直接勾选 -->
          <el-checkbox-group
            :model-value="plainItemIds"
            class="item-group"
            @update:model-value="onPlainItemsChange"
          >
            <el-checkbox
              v-for="it in itemOptions.filter((i) => i.aiops_data_type !== 'list')"
              :key="it.item_id"
              :value="it.item_id"
              class="item-check"
            >
              {{ it.name }}
              <el-tag size="small" effect="plain" :type="it.aiops_data_type === 'number' ? 'primary' : 'info'">
                {{ it.aiops_data_type === 'number' ? '数值' : '文本' }}
              </el-tag>
              <span v-if="it.aiops_cycle" class="opt-meta">{{ it.aiops_cycle }}s</span>
            </el-checkbox>
          </el-checkbox-group>

          <!-- list：勾选后展开「列 → 主键行」 -->
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
        </template>
      </div>
    </el-form-item>
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
.list-item {
  margin-top: var(--space-3);
  padding-top: var(--space-3);
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