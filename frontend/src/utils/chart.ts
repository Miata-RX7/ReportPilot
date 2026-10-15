/**
 * ECharts option 构造（纯函数，便于单测；渲染由 useChart 负责）。
 *
 * 设计约定：
 * - curve 的 xAxis 用 **time**（多序列时间戳可能不对齐，category 轴会误导）
 * - 缺失小时不补点：后端不给 null，前端也不插值
 * - 颜色取 Design Token 的 --chart-series-N（与 matplotlib 阶段 3 同一套色板）
 */

/** 曲线色板（与 CSS 变量 --chart-series-1..8 对应） */
export const SERIES_COLORS = [
  '#2563eb',
  '#0d9488',
  '#d97706',
  '#dc2626',
  '#7c3aed',
  '#0891b2',
  '#65a30d',
  '#db2777',
]

export interface CurveOptionInput {
  series: { name: string; points: [number, number][] }[]
  unit?: string
}

/** 折线图 option：x 轴 time（毫秒），多序列独立成线 */
/** Y 轴紧凑刻度：1234567 -> 1.23M（避免 9 位数字在窄容器里被截断成 "000,000"） */
export function compactNumber(v: number): string {
  const abs = Math.abs(v)
  const round2 = (x: number) => Math.round(x * 100) / 100
  if (abs >= 1e9) return `${round2(v / 1e9)}G`
  if (abs >= 1e6) return `${round2(v / 1e6)}M`
  if (abs >= 1e3) return `${round2(v / 1e3)}K`
  return String(round2(v))
}

export function buildCurveOption(input: CurveOptionInput): Record<string, unknown> {
  const series = input.series ?? []
  return {
    color: SERIES_COLORS,
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0, type: 'scroll' },
    // containLabel + 足够 left：容纳 "123.45M" 这类紧凑刻度
    grid: { left: 24, right: 24, top: 28, bottom: 24, containLabel: true },
    xAxis: {
      // time 轴：多序列时间戳可能不对齐，category 轴会误导
      type: 'time',
      axisLabel: { formatter: '{MM}-{DD}', hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      name: input.unit || '',
      axisLabel: { formatter: compactNumber },
      splitLine: { lineStyle: { type: 'dashed' } },
    },
    series: series.map((s, i) => ({
      name: s.name,
      type: 'line',
      showSymbol: false,
      // 秒 -> 毫秒（ECharts time 轴按 ms）
      data: (s.points ?? []).map(([ts, v]) => [ts * 1000, v]),
      lineStyle: { width: 2 },
      itemStyle: { color: SERIES_COLORS[i % SERIES_COLORS.length] },
    })),
  }
}

export interface PieOptionInput {
  upPct: number
  /** 计数为 0 的一侧不画标签（与后端 PNG 行为一致） */
  upCount?: number
  downCount?: number
  downPct: number
  upLabel?: string
  downLabel?: string
}

/**
 * 饼图 option：UP 绿 / DOWN 红。
 *
 * 标签口径与后端 chart_renderer 一致：
 * - 名称优先用后端 pie payload 下发的 up_label/down_label（1/0 型 -> 通/断）
 * - 计数为 0 的一侧不画外侧标签（ECharts 会把它压在 12 点位置、叠在相邻扇区上，
 *   与后端 PNG 已修正的行为一致）；0 值信息由图例表达
 */
export function buildPieOption(input: PieOptionInput): Record<string, unknown> {
  const upName = input.upLabel ?? 'UP'
  const downName = input.downLabel ?? 'DOWN'
  const showUp = (input.upCount ?? 1) > 0
  const showDown = (input.downCount ?? 1) > 0
  return {
    color: ['#16a34a', '#dc2626'],
    tooltip: { trigger: 'item', formatter: '{b}: {c}%' },
    legend: { bottom: 0 },
    series: [
      {
        type: 'pie',
        radius: ['42%', '68%'],
        center: ['50%', '46%'],
        avoidLabelOverlap: true,
        label: {
          formatter: (p: { name: string; value: number; percent: number }) => {
            const hide = (p.name === upName && !showUp) || (p.name === downName && !showDown)
            if (hide) return ''
            return `${p.name}\n${p.percent.toFixed(1)}%`
          },
        },
        labelLine: { show: true },
        data: [
          {
            name: upName,
            value: input.upPct,
            itemStyle: { color: '#16a34a' },
            label: { show: showUp },
            labelLine: { show: showUp },
          },
          {
            name: downName,
            value: input.downPct,
            itemStyle: { color: '#dc2626' },
            label: { show: showDown },
            labelLine: { show: showDown },
          },
        ],
      },
    ],
  }
}
