/**
 * cron 表达式工具：5 段式（分 时 日 月 周）。
 *
 * 后端用 APScheduler 的 `CronTrigger.from_crontab` 求值，这里做两件事：
 * 1. 人类可读摘要（列表展示用，避免用户面对裸表达式）
 * 2. "下次执行"预览 —— **必须与后端同一套求值口径**，因此由后端
 *    返回 `next_run_at` 展示，本文件只做兜底估算（时区按报告时区）。
 *
 * 约定：cron 的时区是报告时区（默认 Asia/Hong_Kong），不是浏览器本地时区。
 */

export const CRON_PRESETS: { label: string; value: string }[] = [
  { label: '每月 1 号 09:00', value: '0 9 1 * *' },
  { label: '每月 5 号 09:00', value: '0 9 5 * *' },
  { label: '每月 10 号 18:00', value: '0 18 10 * *' },
  { label: '每周一 09:00', value: '0 9 * * 1' },
  { label: '每周五 18:00', value: '0 18 * * 5' },
]

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

/** 粗校验 5 段式（真正的校验在后端，这里只为即时反馈） */
export function isValidCron(expr: string): boolean {
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return false
  const ranges: [number, number][] = [
    [0, 59],
    [0, 23],
    [1, 31],
    [1, 12],
    [0, 7],
  ]
  return parts.every((p, i) => {
    const [lo, hi] = ranges[i]
    // 支持 *、a-b、a,b、*\/n、a-b/n
    return p
      .split(',')
      .every((seg) => {
        const [range, stepRaw] = seg.split('/')
        if (stepRaw !== undefined && !/^\d+$/.test(stepRaw)) return false
        if (range === '*') return true
        const m = /^(\d+)(?:-(\d+))?$/.exec(range)
        if (!m) return false
        const a = Number(m[1])
        const b = m[2] === undefined ? a : Number(m[2])
        return a >= lo && a <= hi && b >= lo && b <= hi && a <= b
      })
  })
}

/**
 * 人类可读摘要。
 * 只处理常见形态（* / 数字 / 逗号列表），复杂表达式回退为原串。
 */
export function cronSummary(expr: string): string {
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return expr
  const [min, hour, dom, mon, dow] = parts
  const time = `${hour === '*' ? '' : pad(Number(hour))}:${pad(Number(min))}`
  const datePart = (() => {
    if (dom === '*' && dow === '*') return '每天'
    if (dom === '*' && dow !== '*') {
      // 0 与 7 都表示周日
      const days = dow
        .split(',')
        .map((d) => `周${WEEKDAYS[d === '7' ? 0 : Number(d)] ?? d}`)
        .join('、')
      return `每${days}`
    }
    if (dom !== '*' && dow === '*') {
      const d = dom
        .split(',')
        .map((x) => `${Number(x)} 号`)
        .join('、')
      return d
    }
    return `${dom} 日 ${dow}`
  })()
  const monPart = mon === '*' ? '' : `${Number(mon)} 月 `
  return `${monPart}${datePart} ${time || '00:00'}`.trim()
}

/**
 * 下次执行预览（**估算**）。
 * 正式展示优先用后端返回的 `next_run_at`（口径一致）；这里用于表单里
 * 选了 cron 但还没保存时给个即时反馈。
 */
export function nextRunPreview(expr: string, from = new Date()): string {
  if (!isValidCron(expr)) return '表达式无效'
  const parts = expr.trim().split(/\s+/)
  const [min, hour, dom, mon, dow] = parts.map((p) => (p === '*' ? null : Number(p)))
  const d = new Date(from.getTime())
  d.setSeconds(0, 0)
  // 从"下一分钟"开始逐分钟步进，最多找 366 天（366 * 24 * 60 次）。
  // 注意：步进只在循环内做一次，否则会跳过"当前时刻 + 1 分钟"这个候选。
  const MAX_MINUTES = 366 * 24 * 60
  for (let i = 0; i < MAX_MINUTES; i++) {
    d.setMinutes(d.getMinutes() + 1)
    const monthOk = mon === null || d.getMonth() + 1 === mon
    const domOk = dom === null || d.getDate() === dom
    const dowRaw = dow === null ? null : dow === 7 ? 0 : dow
    const dowOk = dowRaw === null || d.getDay() === dowRaw
    const hourOk = hour === null || d.getHours() === hour
    const minOk = min === null || d.getMinutes() === min
    if (monthOk && domOk && dowOk && hourOk && minOk) {
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
    }
  }
  return '一年内无匹配时间'
}