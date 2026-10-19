import { describe, expect, it } from 'vitest'

import { buildCurveOption, buildPieOption, compactNumber, SERIES_COLORS } from '@/utils/chart'

describe('buildCurveOption', () => {
  it('x 轴用 time（多序列时间戳可能不对齐）', () => {
    const opt = buildCurveOption({ series: [{ name: 'a', points: [[1, 2]] }] }) as any
    expect(opt.xAxis.type).toBe('time')
  })

  it('秒转毫秒（ECharts time 轴按 ms）', () => {
    const opt = buildCurveOption({ series: [{ name: 'a', points: [[1788220800, 2.5]] }] }) as any
    expect(opt.series[0].data).toEqual([[1788220800000, 2.5]])
  })

  it('多序列各自成线并按序取色', () => {
    const opt = buildCurveOption({
      unit: 'Mbps',
      series: [
        { name: '入向', points: [[1, 1]] },
        { name: '出向', points: [[1, 2]] },
      ],
    }) as any
    expect(opt.series).toHaveLength(2)
    expect(opt.series[0].name).toBe('入向')
    expect(opt.series[1].name).toBe('出向')
    expect(opt.series[0].itemStyle.color).toBe(SERIES_COLORS[0])
    expect(opt.series[1].itemStyle.color).toBe(SERIES_COLORS[1])
    expect(opt.yAxis.name).toBe('Mbps')
  })

  it('不补 null：缺失小时原样透传', () => {
    const opt = buildCurveOption({
      series: [{ name: 'a', points: [[3600, 1], [10800, 3]] }],
    }) as any
    // 只有真实点，不含 null 占位
    expect(opt.series[0].data).toHaveLength(2)
    expect(opt.series[0].data.every((d: unknown[]) => d[1] !== null)).toBe(true)
  })

  it('空 series 不报错', () => {
    const opt = buildCurveOption({ series: [] }) as any
    expect(opt.series).toEqual([])
  })
})

describe('buildPieOption', () => {
  it('UP/DOWN 两片 + 百分比标签', () => {
    const opt = buildPieOption({ upPct: 75, downPct: 25 }) as any
    expect(opt.series[0].type).toBe('pie')
    const [up, down] = opt.series[0].data
    expect(up.name).toBe('UP')
    expect(up.value).toBe(75)
    expect(down.name).toBe('DOWN')
    expect(down.value).toBe(25)
    // 语义色：UP 绿 / DOWN 红
    expect(up.itemStyle.color).toBe('#16a34a')
    expect(down.itemStyle.color).toBe('#dc2626')
    // formatter 是函数（按名称判断是否渲染），两片都有值时都输出百分比
    const fmt = opt.series[0].label.formatter
    expect(typeof fmt).toBe('function')
    expect(fmt({ name: 'UP', value: 75, percent: 75 })).toContain('75.0%')
  })

  it('支持自定义标签', () => {
    const opt = buildPieOption({ upPct: 1, downPct: 0, upLabel: '通', downLabel: '断' }) as any
    expect(opt.series[0].data[0].name).toBe('通')
    expect(opt.series[0].data[1].name).toBe('断')
  })
})


describe('compactNumber（Y 轴紧凑刻度）', () => {
  it('大数用 K/M/G，避免 9 位数字在窄容器里被截断', () => {
    expect(compactNumber(1234567)).toBe('1.23M')
    expect(compactNumber(250000)).toBe('250K')
    expect(compactNumber(2000000000)).toBe('2G')
  })

  it('小数与负数', () => {
    expect(compactNumber(0)).toBe('0')
    expect(compactNumber(12.345)).toBe('12.35')
    expect(compactNumber(-1500)).toBe('-1.5K')
  })

  it('小于 1000 不缩写', () => {
    expect(compactNumber(999)).toBe('999')
  })
})

describe('buildCurveOption 坐标轴可读性', () => {
  it('y 轴使用紧凑刻度 + containLabel（避免标签被裁掉）', () => {
    const opt = buildCurveOption({ series: [{ name: 'a', points: [[1, 1]] }] }) as any
    expect(opt.yAxis.axisLabel.formatter).toBe(compactNumber)
    expect(opt.grid.containLabel).toBe(true)
  })

  it('x 轴仍是 time 轴且标签自动避让', () => {
    const opt = buildCurveOption({ series: [] }) as any
    expect(opt.xAxis.type).toBe('time')
    expect(opt.xAxis.axisLabel.hideOverlap).toBe(true)
  })

  // 回归：曾写成 '{MM}-{DD}'，而 ECharts 的「日」token 是小写 {dd}，
  // {DD} 不是合法 token，会被原样输出成 "09-{DD}"
  it('x 轴标签用合法的 ECharts 时间 token', () => {
    const opt = buildCurveOption({ series: [] }) as any
    const f = opt.xAxis.axisLabel.formatter
    expect(f).toBe('{MM}-{dd}')
    expect(String(f)).not.toMatch(/\{DD\}/)
  })
})


describe('buildPieOption 标签口径（与后端 PNG 一致）', () => {
  it('使用后端下发的标签（通/断），不用默认 UP/DOWN', () => {
    const opt = buildPieOption({
      upPct: 94.1,
      downPct: 5.9,
      upCount: 700,
      downCount: 44,
      upLabel: '通',
      downLabel: '断',
    }) as any
    const names = opt.series[0].data.map((d: { name: string }) => d.name)
    expect(names).toEqual(['通', '断'])
  })

  it('缺标签时回退 UP/DOWN', () => {
    const opt = buildPieOption({ upPct: 50, downPct: 50 }) as any
    const names = opt.series[0].data.map((d: { name: string }) => d.name)
    expect(names).toEqual(['UP', 'DOWN'])
  })

  it('计数为 0 的一侧不显示标签（避免压在相邻扇区上）', () => {
    const opt = buildPieOption({
      upPct: 100,
      downPct: 0,
      upCount: 720,
      downCount: 0,
      upLabel: '通',
      downLabel: '断',
    }) as any
    const [up, down] = opt.series[0].data
    expect(up.label.show).toBe(true)
    expect(down.label.show).toBe(false)
    expect(down.labelLine.show).toBe(false)
    // 图例仍带 0 值信息
    expect(opt.legend).toBeTruthy()
  })

  it('两侧都有值时都显示标签', () => {
    const opt = buildPieOption({
      upPct: 94.1,
      downPct: 5.9,
      upCount: 700,
      downCount: 44,
      upLabel: '通',
      downLabel: '断',
    }) as any
    expect(opt.series[0].data.every((d: { label: { show: boolean } }) => d.label.show)).toBe(true)
  })
})
