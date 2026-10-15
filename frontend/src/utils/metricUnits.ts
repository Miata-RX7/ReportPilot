/**
 * 指标「单位」的预置清单 —— 单位选择器的唯一数据源。
 *
 * 交互（Grafana standard options 风格）：单位下拉里按分组挑一个单位，右边勾「换算」，
 * 勾上后选进制（1000 / 1024）。选不到就手写自定义单位，自定义单位勾换算时**必须**填进制。
 *
 * 三条硬规则（踩过坑，勿破）：
 *
 * 1. **只给「基准单位」配 `base`**。选了带前缀的单位（`Kbps` / `MB`）就置灰「换算」勾选框：
 *    自动选档的前缀是**追加**在单位文本前的（`unit_scale.UnitScale.resolve` 里
 *    `f"{prefix}{suffix or base_unit}"`），基准单位是 `Kbps` 时会算出 `KKbps`。
 * 2. `base` 就是该单位的**标准进制**（SI 速率 1000 / IEC 字节 1024），勾换算时由
 *    `standardBaseOf()` 自动预填，用户仍可改。
 * 3. 单位文本只影响**展示口径**，不参与任何计算（`unit_config` 契约见
 *    `backend/app/schemas/metric.py`，后端零改动）。
 *
 * ---
 *
 * ## 后续要扩展单位清单的话术（把下面这段直接粘给 AI）
 *
 * ```
 * 沿用 ReportPilot 指标编辑的单位预置清单：frontend/src/utils/metricUnits.ts 的 METRIC_UNIT_GROUPS。
 * 新增分组：<分组名>，标准进制 <1000 或 1024>，包含单位 <单位1、单位2…>，label 用「符号（中文名）」格式。
 * 带前缀的单位（以 K/M/G/T 或 Ki/Mi/Gi/Ti 开头）不要给 base —— 表示该项不可换算。
 * 同步在 frontend/src/utils/__tests__/metricUnits.spec.ts 增补断言；后端 unit_config 契约冻结，无需改动。
 * ```
 */

/** 换算进制：1000 = SI（K/M/G/T），1024 = IEC（Ki/Mi/Gi/Ti） */
export type UnitBase = 1000 | 1024

export interface UnitPreset {
  /** 落库到 `metric_definitions.unit` 的文本，如 `bps`、`B` */
  value: string
  /** 下拉里显示的名称，形如「符号（中文名）」 */
  label: string
  /**
   * 标准进制。**有值 = 该单位可作为换算基准**；缺省 = 本身已带 K/M/G/T 前缀，
   * 不允许再勾选换算（否则会出现 `KKbps`）。
   */
  base?: UnitBase
}

export interface UnitGroup {
  group: string
  options: UnitPreset[]
}

export const METRIC_UNIT_GROUPS: UnitGroup[] = [
  {
    group: '速率与带宽',
    options: [
      { value: 'bps', label: 'bps（比特/秒）', base: 1000 },
      { value: 'bit/s', label: 'bit/s（比特/秒，同 bps）', base: 1000 },
      { value: 'pps', label: 'pps（包/秒）', base: 1000 },
      { value: 'Kbps', label: 'Kbps（千比特/秒，已带前缀）' },
      { value: 'Mbps', label: 'Mbps（兆比特/秒，已带前缀）' },
      { value: 'Gbps', label: 'Gbps（吉比特/秒，已带前缀）' },
    ],
  },
  {
    group: '字节与存储',
    options: [
      { value: 'B', label: 'B（字节）', base: 1024 },
      { value: 'KB', label: 'KB（千字节，已带前缀）' },
      { value: 'MB', label: 'MB（兆字节，已带前缀）' },
      { value: 'GB', label: 'GB（吉字节，已带前缀）' },
      { value: 'TB', label: 'TB（太字节，已带前缀）' },
    ],
  },
]

/** 无试算结果时「展示口径」预览行用的示例值（5 位整数 + 3 位小数，确定性，便于截图/测试） */
export const EXAMPLE_RAW_VALUE = 12345.678

/** 可换算单位的 `value -> 标准进制` 索引（模块加载时建一次） */
const PRESET_BASE = new Map<string, UnitBase>()
for (const group of METRIC_UNIT_GROUPS) {
  for (const option of group.options) {
    if (option.base) PRESET_BASE.set(option.value, option.base)
  }
}

/** 单位文本里「已带 K/M/G/T（或 Ki/Mi/Gi/Ti）」的前缀；大小写敏感，`bit/s` 不会被误判 */
const SCALE_PREFIX_RE = /^(?:Ki|Mi|Gi|Ti|Pi|Ei|[KMGT])/

function normalizeUnit(unit: string | null | undefined): string {
  return (unit ?? '').trim()
}

/**
 * 命中预置项返回其**标准进制**；自定义单位或不可换算的单位返回 null。
 * 勾选「换算」时用它自动预填进制。
 */
export function standardBaseOf(unit: string | null | undefined): UnitBase | null {
  return PRESET_BASE.get(normalizeUnit(unit)) ?? null
}

/**
 * 单位自身是否已带换算前缀（`Kbps` / `MB` / `MiB` 为 true）。
 * 为 true 时置灰「换算」勾选框 —— 前缀由自动选档追加，再换算会得到 `KKbps`。
 */
export function hasScalePrefix(unit: string | null | undefined): boolean {
  return SCALE_PREFIX_RE.test(normalizeUnit(unit))
}

/**
 * 给带前缀单位推荐一个可换算的基准单位（`Kbps` -> `bps`、`MB` -> `B`）；
 * 剥掉前缀后仍不是预置基准单位时返回 null（提示语不瞎推荐）。
 */
export function baseUnitSuggestion(unit: string | null | undefined): string | null {
  const text = normalizeUnit(unit)
  if (!hasScalePrefix(text)) return null
  const stripped = text.replace(SCALE_PREFIX_RE, '')
  return standardBaseOf(stripped) ? stripped : null
}

/** 该单位是否可作为换算基准（预置项看 `base`，自定义单位一律允许 —— 由用户自己填进制） */
export function isConvertibleUnit(unit: string | null | undefined): boolean {
  const text = normalizeUnit(unit)
  return text !== '' && !hasScalePrefix(text)
}