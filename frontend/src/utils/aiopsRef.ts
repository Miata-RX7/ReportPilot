/**
 * AIOps 定位串编解码与列能力推导 —— **必须与后端 `services/aiops_keys.py` 逐字一致**。
 *
 * 为什么需要这一层：AIOps 的一个「可取数对象」不是单个 id，而是坐标四元组
 * `(实例, 监控项, 列, 主键行)`。主键行是任意字符串（实测出现过
 * `GigabitEthernet1/0/1`、`Ethernet-Trunk1`、`CNOOC-(HK-Internet_Center)`），
 * 含 `:` `/` 空格括号，无法与数字用分隔符安全拼接。
 *
 * 定位串格式（`:` 分隔，主键行走 base64url 无padding）：
 *
 *     number / text 监控项      n:<instance_id>:<item_id>
 *     list 监控项的某行某列      l:<instance_id>:<item_id>:<column_id>:<b64url(row_key)>
 *
 * 前缀用字母开头，与纯数字的 Zabbix item_id 天然区分 —— 存量 Zabbix 配置零改动。
 *
 * 实测结论（2026-10-16，改动前先读 backend/app/services/aiops_keys.py 的注释）：
 * - `item/list` 已返回列的全部元数据，第 5 级不需要额外请求
 * - `source_history` 是**列级能力标志**，不保证每一行有数据
 * - 「该行该列当前值非空」⟺「有历史」，所以行列表按当前值过滤即可
 */

export const AIOPS_PREFIX_PLAIN = 'n'
export const AIOPS_PREFIX_LIST = 'l'

export type AiopsDataType = 'number' | 'text' | 'list'
export type AiopsValueType = 'number' | 'text'
export type MetricType = 'scalar' | 'curve' | 'pie' | 'text' | 'time'

export interface AiopsRef {
  instanceId: number
  itemId: number
  /** 以下两个仅list 型监控项有值 */
  columnId: number | null
  rowKey: string | null
}

export interface AiopsColumn {
  column_id: number
  name: string
  unit: string
  value_type: AiopsValueType
  has_history: boolean
  is_primary: boolean
}

/* ---------------------------------------------------------------- 定位串 */

/** 主键行 -> base64url（无 padding）。UTF-8，支持中文行键。 */
function b64e(raw: string): string {
  const bytes = new TextEncoder().encode(raw)
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** base64url -> 主键行。失败返回 null（UI 不抛异常）。 */
function b64d(token: string): string | null {
  try {
    const pad = '='.repeat((4 - (token.length % 4)) % 4)
    const bin = atob(token.replace(/-/g, '+').replace(/_/g, '/') + pad)
    const bytes = Uint8Array.from(bin, (ch) => ch.charCodeAt(0))
    return new TextDecoder().decode(bytes)
  } catch {
    return null
  }
}

/**
 * 组装稳定定位串。
 *
 * `columnId` 与 `rowKey` **必须同时给或同时不给**，返回 null 表示非法 ——
 * 半截坐标会让 `status` / `history` 的行筛选静默失效，返回跨行聚合值（实测踩过）。
 */
export function encodeRef(
  instanceId: number | string,
  itemId: number | string,
  columnId?: number | string | null,
  rowKey?: string | null,
): string | null {
  const hasColumn = columnId !== undefined && columnId !== null
  const hasRow = rowKey !== undefined && rowKey !== null
  if (hasColumn !== hasRow) return null
  const base = `${instanceId}:${itemId}`
  if (!hasColumn) return `${AIOPS_PREFIX_PLAIN}:${base}`
  return `${AIOPS_PREFIX_LIST}:${base}:${columnId}:${b64e(String(rowKey))}`
}

/** 解析定位串；非法格式返回 null（UI 侧容错，后端会转 422）。 */
export function decodeRef(ref: string): AiopsRef | null {
  if (!ref) return null
  const parts = ref.split(':')
  const toInt = (v: string) => {
    const n = Number(v)
    return Number.isInteger(n) ? n : null
  }
  if (parts[0] === AIOPS_PREFIX_PLAIN && parts.length === 3) {
    const instanceId = toInt(parts[1])
    const itemId = toInt(parts[2])
    if (instanceId === null || itemId === null) return null
    return { instanceId, itemId, columnId: null, rowKey: null }
  }
  if (parts[0] === AIOPS_PREFIX_LIST && parts.length === 5) {
    const instanceId = toInt(parts[1])
    const itemId = toInt(parts[2])
    const columnId = toInt(parts[3])
    const rowKey = b64d(parts[4])
    if (instanceId === null || itemId === null || columnId === null) return null
    if (rowKey === null) return null
    return { instanceId, itemId, columnId, rowKey }
  }
  return null
}

export function isAiopsRef(ref: string): boolean {
  return decodeRef(ref) !== null
}

export function isListRef(ref: AiopsRef | null): boolean {
  return !!ref && ref.columnId !== null && ref.rowKey !== null
}

/* ------------------------------------------------------------ 能力判定表 */

/**
 * 该监控项（或该列）允许的平台 metric_type。
 *
 * | data_type | 列 value_type | source_history | 平台可选       |
 * | --------- | ------------- | -------------- | -------------- |
 * | number    | -             | -              | scalar / curve |
 * | text      | -             | -              | text      |
 * | list      | text          | 0 或 1         | text           |
 * | list      | number        | 1              | scalar / curve |
 * | list      | number        | 0              | scalar         |
 *
 * 「无历史的 number 列只能选数值、不能选曲线」是用户明确要求：它的当前值存在于
 * status 表，但没有时间序列，画曲线必然是空图。
 * **pie 对 AIOps 一律不可用**（无 Zabbix 那种 up/down 值域语义）。
 */
export function allowedMetricTypes(
  dataType: AiopsDataType | null | undefined,
  column?: AiopsColumn | null,
): MetricType[] {
  if (dataType === 'number') return ['scalar', 'curve']
  if (dataType === 'text') return ['text']
  if (dataType === 'list') {
    if (!column) return []
    if (column.value_type === 'text') return ['text']
    return column.has_history ? ['scalar', 'curve'] : ['scalar']
  }
  return []
}

export function supportsCurve(
  dataType: AiopsDataType | null | undefined,
  column?: AiopsColumn | null,
): boolean {
  return allowedMetricTypes(dataType, column).includes('curve')
}

/** 指标类型的中文说明（禁用项 tooltip 与已选项回显用）。 */
export const METRIC_TYPE_LABEL: Record<MetricType, string> = {
  scalar: '数值',
  curve: '曲线图',
  pie: '饼图',
  text: '文本',
  time: '时间',
}

/**
 * 某个指标类型被禁用的原因；`null` 表示没被禁用。
 *
 * 用户要求「鼠标挪上去时有一个提示」，所以文案要具体到**为什么**，
 * 而不是干巴巴一句「不支持」。
 */
export function metricTypeBlockedReason(
  metricType: MetricType,
  dataType: AiopsDataType | null | undefined,
  column?: AiopsColumn | null,
): string | null {
  if (metricType === 'pie') {
    return 'AIOps 数据源不支持饼图：没有 Zabbix 那样的 UP/DOWN 值域语义'
  }
  if (!dataType) return null
  const allowed = allowedMetricTypes(dataType, column)
  if (allowed.includes(metricType)) return null
  if (metricType === 'curve' && dataType === 'list' && column && !column.has_history) {
    return '该列没有历史数据（source_history=0），只有当前值，画曲线必然是空图；可改用「数值」'
  }
  if (metricType === 'text' && dataType === 'list' && column?.value_type === 'number') {
    return '该列是数值类型，请改用「数值」或「曲线图」'
  }
  // 文本型不能做数值/曲线：既包括整个监控项就是 text，也包括 list 里的 text 列
  // （端口名称、状态这类列，填进数值指标没有意义）
  const textLike =
    dataType === 'text' || (dataType === 'list' && column?.value_type === 'text')
  if ((metricType === 'scalar' || metricType === 'curve') && textLike) {
    return dataType === 'text'
      ? '该监控项是文本类型，不能做数值或曲线'
      : '该列是文本类型（如端口名称、状态），不能做数值或曲线'
  }
  if (metricType === 'time') return '时间类型不取数，与数据源无关'
  return `该列不支持「${METRIC_TYPE_LABEL[metricType]}」`
}

/* ------------------------------------------------------------ 展示辅助 */

/** 列的展示名：`入流量 (auto1000 bps)`，无单位时只给名字。 */
export function columnLabel(column: AiopsColumn): string {
  return column.unit ? `${column.name} (${column.unit})` : column.name
}

/** 定位串的紧凑展示：`端口表/ 入流量 / GigabitEthernet1/0/1`。 */
export function refLabel(ref: AiopsRef, itemName?: string, column?: AiopsColumn | null): string {
  if (!isListRef(ref)) return itemName || `item-${ref.itemId}`
  const parts = [itemName || `item-${ref.itemId}`]
  if (column) parts.push(columnLabel(column))
  if (ref.rowKey) parts.push(ref.rowKey)
  return parts.join(' / ')
}

/* ------------------------------------------------------------ 选择项 */

/**
 * 级联里选中的一个「取数对象」。
 *
 * `ref` 是要写进 `query_config.item_ids` / `series[].item_id` 的定位串；
 * 其余字段只服务于**回显与提示**（刷新页面后要能把 ref 还原成人看得懂的东西）。
 */
export interface AiopsPick {
  /** 稳定定位串，落库用这个 */
  ref: string
  /** 展示名，如「端口表 / 入流量 (bps) / GigabitEthernet1/0/1」 */
  label: string
  /** 监控项 id */
  itemId: string
  /** 监控项名 */
  itemName: string
  /** list 型才有的列 */
  column?: AiopsColumn | null
  /** list 型才有的主键行 */
  rowKey?: string | null
}

/** 依据选择构造 picks（列或行缺失时返回空数组，表示还没选全）。 */
export function buildPicks(
  instanceId: string,
  items: { id: string; name: string; dataType: AiopsDataType; columns?: AiopsColumn[] | null }[],
  listSelections: Record<string, { columnId: string; rowKeys: string[] }>,
): AiopsPick[] {
  const out: AiopsPick[] = []
  for (const item of items) {
    if (item.dataType === 'list') {
      const sel = listSelections[item.id]
      if (!sel?.columnId) continue
      const column = (item.columns ?? []).find((c) => String(c.column_id) === String(sel.columnId))
      if (!column) continue
      for (const rowKey of sel.rowKeys) {
        const ref = encodeRef(instanceId, item.id, column.column_id, rowKey)
        if (!ref) continue
        out.push({
          ref,
          label: refLabel(
            { instanceId: Number(instanceId), itemId: Number(item.id), columnId: column.column_id, rowKey },
            item.name,
            column,
          ),
          itemId: item.id,
          itemName: item.name,
          column,
          rowKey,
        })
      }
    } else {
      const ref = encodeRef(instanceId, item.id)
      if (!ref) continue
      out.push({
        ref,
        label: item.name,
        itemId: item.id,
        itemName: item.name,
        column: null,
        rowKey: null,
      })
    }
  }
  return out
}

/** 把已落库的定位串还原成级联回显所需的分组结构。 */
export function picksToSelections(
  refs: string[],
  itemNameOf: (itemId: string) => string,
): {
  plainItemIds: string[]
  listSelections: Record<string, { columnId: string; rowKeys: string[] }>
} {
  const plainItemIds: string[] = []
  const listSelections: Record<string, { columnId: string; rowKeys: string[] }> = {}
  for (const raw of refs) {
    const ref = decodeRef(raw)
    if (!ref) continue
    if (!isListRef(ref)) {
      if (!plainItemIds.includes(String(ref.itemId))) plainItemIds.push(String(ref.itemId))
      continue
    }
    const itemId = String(ref.itemId)
    const columnId = String(ref.columnId)
    if (!listSelections[itemId]) listSelections[itemId] = { columnId, rowKeys: [] }
    if (listSelections[itemId].columnId !== columnId) {
      // 同一监控项选了不同列的行：以最后一个为准（正常流程下不会出现，
      // 因为选新列时前端会清掉旧行）
      listSelections[itemId] = { columnId, rowKeys: [] }
    }
    if (ref.rowKey && !listSelections[itemId].rowKeys.includes(ref.rowKey)) {
      listSelections[itemId].rowKeys.push(ref.rowKey)
    }
  }
  void itemNameOf
  return { plainItemIds, listSelections }
}