/**
 * 时间类型的格式渲染（前端侧）。
 *
 * ⚠️ 与后端 `app/services/calc_engine.py::format_time_modules` **语义必须一致**，
 * 两侧都有单测锁定同一批用例。改动任一侧都要同步另一侧。
 *
 * 模块词表（与后端 TIME_MODULES 对应）：
 * - 字段：年 月 日 时 分 秒
 * - 字面量：空格 - / : .
 */

/** 字段模块（渲染为数字） */
export const TIME_FIELD_MODULES = ['年', '月', '日', '时', '分', '秒'] as const

/** 字面量模块（原样输出） */
export const TIME_LITERAL_MODULES = ['空格', '-', '/', ':', '.'] as const

/** 全部可用模块（面板展示顺序 = 字段先、字面量后） */
export const TIME_MODULES: string[] = [...TIME_FIELD_MODULES, ...TIME_LITERAL_MODULES]

/** 字段模块 -> 取值函数 */
const FIELD_GETTERS: Record<string, (d: Date) => number> = {
  年: (d) => d.getFullYear(),
  月: (d) => d.getMonth() + 1,
  日: (d) => d.getDate(),
  时: (d) => d.getHours(),
  分: (d) => d.getMinutes(),
  秒: (d) => d.getSeconds(),
}

/** 字面量模块 -> 输出字符 */
const LITERAL_CHARS: Record<string, string> = {
  空格: ' ',
  '-': '-',
  '/': '/',
  ':': ':',
  '.': '.',
}

export type TimeOffsetUnit = 'day' | 'month'

/** 快速偏移选项（与后端 TIME_QUICK_OFFSETS 一致，改动需同步） */
export const TIME_QUICK_OPTIONS: { value: TimeQuickOffset; label: string }[] = [
  { value: 'last_week_monday', label: '上周周一' },
  { value: 'last_week_sunday', label: '上周周末' },
  { value: 'last_month_first', label: '上月一号' },
  { value: 'last_month_end', label: '上月月末' },
]

export type TimeQuickOffset = 'last_week_monday' | 'last_week_sunday' | 'last_month_first' | 'last_month_end'

function pad(value: number, width: number): string {
  return String(value).padStart(width, '0')
}

/**
 * 按模块列表渲染时间。
 * - 年恒 4 位；月/日/时/分/秒在 zeroPad 时补零（2026-09），否则不补（2026-9）
 * - 未知模块原样输出（不静默吞掉用户配置）
 */
export function formatTimeModules(
  modules: readonly string[],
  when: Date,
  zeroPad = true,
): string {
  let out = ''
  for (const m of modules) {
    const getter = FIELD_GETTERS[m]
    if (getter) {
      const v = getter(when)
      out += m === '年' ? pad(v, 4) : zeroPad ? pad(v, 2) : String(v)
      continue
    }
    out += LITERAL_CHARS[m] ?? m
  }
  return out
}

/**
 * 按偏移量平移日期。
 * 月偏移用「设置日期」而非 Date 的 setMonth，避免 1-31 减一月溢出到 3 月
 * （与后端 relativedelta 的月末夹取行为对齐：2026-03-31 -1月 -> 2026-02-28）。
 */
export function shiftTime(base: Date, value: number, unit: TimeOffsetUnit): Date {
  if (!value) return new Date(base.getTime())
  const d = new Date(base.getTime())
  if (unit === 'day') {
    d.setDate(d.getDate() + value)
    return d
  }
  const day = d.getDate()
  d.setDate(1)
  d.setMonth(d.getMonth() + value)
  // 目标月天数（setMonth 后 d 已落在目标月，用下月 0 号反推天数更稳）
  const lastDay = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate()
  d.setDate(Math.min(day, lastDay))
  return d
}

/** 偏移量的中文描述（与后端 TimeConfig.label 一致） */
export function timeOffsetLabel(value: number, unit: TimeOffsetUnit): string {
  if (!value) return '不偏移'
  return `${value > 0 ? '+' : ''}${value}${unit === 'day' ? '天' : '月'}`
}




/**
 * 快速偏移：按**当前时刻**解析为具体日期。
 *
 * 与后端 `calc_engine.quick_target_time` 语义必须一致（两侧都有同批单测）：
 * - 上周周一/周末：本周一往前 7 天 / 该日+6（即上一周的周日）
 * - 上月一号：本月 1 号往前一个月；上月月末：本��� 1 号减 1 天
 */
export function quickTargetTime(base: Date, kind: TimeQuickOffset): Date {
  const d = new Date(base.getTime())
  switch (kind) {
    case 'last_week_monday':
      d.setDate(d.getDate() - (d.getDay() === 0 ? 6 : d.getDay() - 1) - 7)
      return d
    case 'last_week_sunday':
      d.setDate(d.getDate() - (d.getDay() === 0 ? 6 : d.getDay() - 1) + 6 - 7)
      return d
    case 'last_month_first': {
      // 上月一号：恒为 1 号，不携带基准日的"日"
      d.setDate(1)
      d.setMonth(d.getMonth() - 1)
      return d
    }
    case 'last_month_end': {
      d.setDate(1)
      d.setMonth(d.getMonth() - 1)
      const lastDay = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate()
      d.setDate(lastDay)
      return d
    }
    default:
      return d
  }
}

/** 偏移描述（手动偏移用 +N天/-1月；快速偏移用中文标签） */
export function timeOffsetText(
  mode: 'manual' | 'quick',
  value: number,
  unit: TimeOffsetUnit,
  quick: TimeQuickOffset | null,
): string {
  if (mode === 'quick') {
    return TIME_QUICK_OPTIONS.find((o) => o.value === quick)?.label ?? '快速偏移'
  }
  return timeOffsetLabel(value, unit)
}

/** 目标时间：按偏移方式解析（手动 or 快速） */
export function resolveTimeTarget(
  base: Date,
  offset: {
    mode: 'manual' | 'quick'
    value: number
    unit: TimeOffsetUnit
    quick: TimeQuickOffset | null
  },
): Date {
  if (offset.mode === 'quick' && offset.quick) return quickTargetTime(base, offset.quick)
  return shiftTime(base, offset.value, offset.unit)
}

/** 实时示例：**只输出最终格式结果**（不展示基准/偏移/目标等推导过程） */
export function timeFormatPreview(
  modules: readonly string[],
  zeroPad: boolean,
  offset: {
    mode: 'manual' | 'quick'
    value: number
    unit: TimeOffsetUnit
    quick: TimeQuickOffset | null
  },
  now: Date = new Date(),
): { text: string; offsetText: string } {
  const target = resolveTimeTarget(now, offset)
  return {
    text: formatTimeModules(modules, target, zeroPad),
    offsetText: timeOffsetText(offset.mode, offset.value, offset.unit, offset.quick),
  }
}
