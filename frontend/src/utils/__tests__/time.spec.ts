import { describe, expect, it } from 'vitest'

import {
  TIME_MODULES,
  TIME_QUICK_OPTIONS,
  formatTimeModules,
  quickTargetTime,
  resolveTimeTarget,
  shiftTime,
  timeFormatPreview,
  timeOffsetLabel,
  timeOffsetText,
} from '@/utils/time'

/** 与后端 calc_engine 测试同批用例，保证两侧语义一致 */
const d = (y: number, mo: number, day: number, h = 0, mi = 0, s = 0) =>
  new Date(y, mo - 1, day, h, mi, s)

describe('TIME_MODULES 词表', () => {
  it('字段 6 个 + 字面量 5 个，与后端一致', () => {
    expect(TIME_MODULES).toEqual(['年', '月', '日', '时', '分', '秒', '空格', '-', '/', ':', '.'])
  })
})

describe('formatTimeModules', () => {
  it('用户示例：2026-10 减 1 月 -> 2026-09（格式化 + 偏移组合）', () => {
    // formatTimeModules 只负责格式化，偏移由 shiftTime 负责
    const target = shiftTime(d(2026, 10, 8), -1, 'month')
    expect(formatTimeModules(['年', '-', '月'], target, true)).toBe('2026-09')
  })

  it('补零：各字段 2 位（年 4 位）', () => {
    expect(formatTimeModules(['年', '年', '-', '月', '-', '日'], d(2026, 1, 5))).toBe(
      '20262026-01-05',
    )
    expect(formatTimeModules(['时', ':', '分', ':', '秒'], d(2026, 1, 5, 9, 8, 7))).toBe(
      '09:08:07',
    )
  })

  it('不补零：月日时分秒直接输出', () => {
    expect(formatTimeModules(['年', '-', '月', '-', '日'], d(2026, 1, 5), false)).toBe(
      '2026-1-5',
    )
    expect(formatTimeModules(['时', ':', '分'], d(2026, 1, 5, 9, 8), false)).toBe('9:8')
  })

  it('年始终 4 位（不补零开关不影响年）', () => {
    expect(formatTimeModules(['年'], d(999, 1, 1), false)).toBe('0999')
    expect(formatTimeModules(['年'], d(2026, 1, 1), true)).toBe('2026')
  })

  it('空格模块输出真实空格', () => {
    expect(formatTimeModules(['年', '空格', '月'], d(2026, 10, 8))).toBe('2026 10')
  })

  it('斜杠/冒号/点分隔符', () => {
    expect(formatTimeModules(['年', '/', '月', '/', '日'], d(2026, 10, 8))).toBe('2026/10/08')
    expect(formatTimeModules(['年', '.', '月'], d(2026, 10, 8))).toBe('2026.10')
  })

  it('模块可重复（[年, 年] -> 20262026）', () => {
    expect(formatTimeModules(['年', '年'], d(2026, 10, 8))).toBe('20262026')
  })

  it('未知模块原样输出，不静默吞掉', () => {
    expect(formatTimeModules(['年', 'Q', '月'], d(2026, 10, 8))).toBe('2026Q10')
  })

  it('空模块列表 -> 空串（后端 schema 已禁空，这里只保证不抛异常）', () => {
    expect(formatTimeModules([], d(2026, 10, 8))).toBe('')
  })
})

describe('shiftTime', () => {
  it('天偏移正负', () => {
    expect(formatTimeModules(['年', '-', '月', '-', '日'], shiftTime(d(2026, 10, 8), 3, 'day'))).toBe(
      '2026-10-11',
    )
    expect(formatTimeModules(['年', '-', '月', '-', '日'], shiftTime(d(2026, 10, 8), -1, 'day'))).toBe(
      '2026-10-07',
    )
  })

  it('月偏移正负（跨年）', () => {
    expect(formatTimeModules(['年', '-', '月'], shiftTime(d(2026, 10, 8), -1, 'month'))).toBe(
      '2026-09',
    )
    expect(formatTimeModules(['年', '-', '月'], shiftTime(d(2026, 1, 8), -1, 'month'))).toBe(
      '2025-12',
    )
    expect(formatTimeModules(['年', '-', '月'], shiftTime(d(2026, 12, 8), 1, 'month'))).toBe(
      '2027-01',
    )
  })

  it('月末夹取：3-31 减 1 月 -> 2-28（不溢出到 3 月）', () => {
    expect(formatTimeModules(['年', '-', '月', '-', '日'], shiftTime(d(2026, 3, 31), -1, 'month'))).toBe(
      '2026-02-28',
    )
    // 闰年 2 月
    expect(formatTimeModules(['年', '-', '月', '-', '日'], shiftTime(d(2028, 3, 31), -1, 'month'))).toBe(
      '2028-02-29',
    )
  })

  it('偏移 0 返回等值副本（不改原对象）', () => {
    const base = d(2026, 10, 8)
    const out = shiftTime(base, 0, 'day')
    expect(out.getTime()).toBe(base.getTime())
    expect(out).not.toBe(base)
  })
})

describe('timeOffsetLabel', () => {
  it('0 -> 不偏移；正数带 +；单位中文', () => {
    expect(timeOffsetLabel(0, 'day')).toBe('不偏移')
    expect(timeOffsetLabel(-1, 'month')).toBe('-1月')
    expect(timeOffsetLabel(3, 'day')).toBe('+3天')
  })
})

describe('quickTargetTime（快速偏移，与后端同批基准日）', () => {
  const d = (y: number, mo: number, day: number) => new Date(y, mo - 1, day, 10, 0, 0)
  const ymd = (x: Date) => formatTimeModules(['年', '-', '月', '-', '日'], x)

  it('基准 2026-10-09（周五）', () => {
    const base = d(2026, 10, 9)
    expect(ymd(quickTargetTime(base, 'last_week_monday'))).toBe('2026-09-28')
    expect(ymd(quickTargetTime(base, 'last_week_sunday'))).toBe('2026-10-04')
    expect(ymd(quickTargetTime(base, 'last_month_first'))).toBe('2026-09-01')
    expect(ymd(quickTargetTime(base, 'last_month_end'))).toBe('2026-09-30')
  })

  it('基准是周一（2026-10-05）时结果不变', () => {
    const base = d(2026, 10, 5)
    expect(ymd(quickTargetTime(base, 'last_week_monday'))).toBe('2026-09-28')
    expect(ymd(quickTargetTime(base, 'last_week_sunday'))).toBe('2026-10-04')
  })

  it('基准是周日（2026-10-11）时上周仍是 09-28~10-04', () => {
    const base = d(2026, 10, 11)
    expect(ymd(quickTargetTime(base, 'last_week_monday'))).toBe('2026-09-28')
    expect(ymd(quickTargetTime(base, 'last_week_sunday'))).toBe('2026-10-04')
  })

  it('跨月：基准 2026-01-31 -> 上月 2025-12', () => {
    const base = d(2026, 1, 31)
    expect(ymd(quickTargetTime(base, 'last_month_first'))).toBe('2025-12-01')
    expect(ymd(quickTargetTime(base, 'last_month_end'))).toBe('2025-12-31')
  })

  it('2 月末（2026-03-01）-> 2026-02-28', () => {
    const base = d(2026, 3, 1)
    expect(ymd(quickTargetTime(base, 'last_month_end'))).toBe('2026-02-28')
  })

  it('四个选项标签齐全', () => {
    expect(TIME_QUICK_OPTIONS.map((o) => o.label)).toEqual([
      '上周周一',
      '上周周末',
      '上月一号',
      '上月月末',
    ])
  })
})

describe('timeOffsetText / resolveTimeTarget', () => {
  const d = (y: number, mo: number, day: number) => new Date(y, mo - 1, day, 10, 0, 0)

  it('手动偏移 -> +N天/-N月；0 -> 不偏移', () => {
    expect(timeOffsetText('manual', 3, 'day', null)).toBe('+3天')
    expect(timeOffsetText('manual', -1, 'month', null)).toBe('-1月')
    expect(timeOffsetText('manual', 0, 'day', null)).toBe('不偏移')
  })

  it('快速偏移 -> 中文标签', () => {
    expect(timeOffsetText('quick', 0, 'day', 'last_week_monday')).toBe('上周周一')
    expect(timeOffsetText('quick', 0, 'day', 'last_month_end')).toBe('上月月末')
  })

  it('resolveTimeTarget 按模式分派', () => {
    const base = d(2026, 10, 9)
    const manual = resolveTimeTarget(base, { mode: 'manual', value: -1, unit: 'month', quick: null })
    const quick = resolveTimeTarget(base, { mode: 'quick', value: 0, unit: 'day', quick: 'last_month_first' })
    expect(formatTimeModules(['年', '-', '月'], manual)).toBe('2026-09')
    expect(formatTimeModules(['年', '-', '月'], quick)).toBe('2026-09')
  })

  it('quick 模式未选时回退到手动的 0（不报错）', () => {
    const base = d(2026, 10, 9)
    const t = resolveTimeTarget(base, { mode: 'quick', value: 0, unit: 'day', quick: null })
    expect(formatTimeModules(['年', '-', '月'], t)).toBe('2026-10')
  })
})

describe('timeFormatPreview（只输出最终格式结果）', () => {
  const d = (y: number, mo: number, day: number) => new Date(y, mo - 1, day, 14, 30, 0)

  it('手动 -1 月 -> 2026-09', () => {
    const p = timeFormatPreview(['年', '-', '月'], true, {
      mode: 'manual',
      value: -1,
      unit: 'month',
      quick: null,
    }, d(2026, 10, 9))
    expect(p.text).toBe('2026-09')
    // 只返回结果与偏移描述，不再暴露基准/目标
    expect(Object.keys(p).sort()).toEqual(['offsetText', 'text'])
  })

  it('快速偏移：上月一号 + 年-月-日 -> 2026-09-01', () => {
    const p = timeFormatPreview(['年', '-', '月', '-', '日'], true, {
      mode: 'quick',
      value: 0,
      unit: 'day',
      quick: 'last_month_first',
    }, d(2026, 10, 9))
    expect(p.text).toBe('2026-09-01')
    expect(p.offsetText).toBe('上月一号')
  })

  it('不补零时同样生效', () => {
    const p = timeFormatPreview(['年', '-', '月', '-', '日'], false, {
      mode: 'quick',
      value: 0,
      unit: 'day',
      quick: 'last_month_end',
    }, d(2026, 10, 9))
    expect(p.text).toBe('2026-9-30')
  })

  it('空格式 -> 空串', () => {
    const p = timeFormatPreview([], true, { mode: 'manual', value: 0, unit: 'day', quick: null })
    expect(p.text).toBe('')
  })
})
