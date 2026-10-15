/** 显示格式化工具（仅格式化展示，不做数值计算；指标数值由后端按 display_precision 处理） */

/** ISO 时间 -> "YYYY-MM-DD HH:mm"（本地时区） */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '-'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '-'
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

/** 取当前月份的上一个月，"YYYY-MM"（月报默认统计周期） */
export function previousPeriod(from: Date = new Date()): string {
  const d = new Date(from.getFullYear(), from.getMonth() - 1, 1)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`
}

// ---------------------------------------------------------------------------
// 周周期（"YYYY-Www"，ISO 周，与后端 period_utils 同口径）
// ---------------------------------------------------------------------------

/** ISO 周编码（"YYYY-Www"） */
const WEEK_PERIOD_RE = /^(\d{4})-W(\d{2})$/

/** 是否周周期编码 */
export function isWeekPeriod(period: string | null | undefined): boolean {
  return WEEK_PERIOD_RE.test(period ?? '')
}

/** ISO 周第 1 周的周一（1 月 4 日所在周的周一） */
function isoWeek1Monday(year: number): Date {
  const jan4 = new Date(year, 0, 4)
  const offset = (jan4.getDay() + 6) % 7 // 周一=0
  return new Date(year, 0, 4 - offset)
}

/** 某日期所在的 ISO 周编码（本地日期，周一为起点） */
export function weekOf(date: Date = new Date()): string {
  const d = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  // ISO 归属年由该周的周四决定
  const thursday = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 3 - ((d.getDay() + 6) % 7))
  const isoYear = thursday.getFullYear()
  const monday1 = isoWeek1Monday(isoYear)
  const days = Math.round((+thursday - +monday1) / 86400000)
  const week = Math.floor(days / 7) + 1
  return `${isoYear}-W${String(week).padStart(2, '0')}`
}

/** 取上一个 ISO 周，"YYYY-Www"（周报默认统计周期） */
export function previousWeek(from: Date = new Date()): string {
  return weekOf(new Date(from.getFullYear(), from.getMonth(), from.getDate() - 7))
}

/** ISO 周编码 -> 该周周一（本地 Date）；非周编码返回 null */
export function weekStartDate(period: string | null | undefined): Date | null {
  const m = WEEK_PERIOD_RE.exec(period ?? '')
  if (!m) return null
  const monday = new Date(isoWeek1Monday(Number(m[1])))
  monday.setDate(monday.getDate() + (Number(m[2]) - 1) * 7)
  return monday
}

/** Date -> "YYYY-MM-DD"（本地日期；不走 toISOString，避免时区位移） */
export function toDateString(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** "YYYY-MM-DD" -> 本地 Date；非法返回 null */
export function parseDateString(value: string | null | undefined): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value ?? '')
  if (!m) return null
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
  return Number.isNaN(d.getTime()) ? null : d
}

/** 周期编码 -> 人读标签（周带区间；月原样返回） */
export function periodLabel(period: string | null | undefined): string {
  if (!period) return '-'
  const m = WEEK_PERIOD_RE.exec(period)
  const start = weekStartDate(period)
  if (!m || !start) return period
  const end = new Date(start)
  end.setDate(end.getDate() + 6)
  const pad = (n: number) => String(n).padStart(2, '0')
  const fmt = (d: Date) => `${pad(d.getMonth() + 1)}/${pad(d.getDate())}`
  return `${Number(m[1])}年第${Number(m[2])}周（${fmt(start)}–${fmt(end)}）`
}
