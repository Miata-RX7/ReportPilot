import { describe, expect, it } from 'vitest'

import {
  formatDateTime,
  isWeekPeriod,
  parseDateString,
  periodLabel,
  previousPeriod,
  previousWeek,
  toDateString,
  weekOf,
  weekStartDate,
} from '@/utils/format'

describe('formatDateTime', () => {
  it('格式化 ISO 时间', () => {
    const iso = new Date(2026, 8, 30, 9, 5).toISOString()
    expect(formatDateTime(iso)).toBe('2026-09-30 09:05')
  })

  it('空值与非法值返回 -', () => {
    expect(formatDateTime(null)).toBe('-')
    expect(formatDateTime('')).toBe('-')
    expect(formatDateTime('not-a-date')).toBe('-')
  })
})

describe('previousPeriod', () => {
  it('普通月份取上月', () => {
    expect(previousPeriod(new Date(2026, 8, 30))).toBe('2026-08')
  })

  it('1 月跨年取上年 12 月', () => {
    expect(previousPeriod(new Date(2026, 0, 15))).toBe('2025-12')
  })
})

describe('周周期工具', () => {
  it('isWeekPeriod 只认 YYYY-Www', () => {
    expect(isWeekPeriod('2026-W41')).toBe(true)
    expect(isWeekPeriod('2026-09')).toBe(false)
    expect(isWeekPeriod(null)).toBe(false)
  })

  it('weekOf 按 ISO 周归属（周三落在本周）', () => {
    expect(weekOf(new Date(2026, 9, 7))).toBe('2026-W41')
  })

  it('weekOf 周日仍属本周（周一为起点）', () => {
    expect(weekOf(new Date(2026, 9, 11))).toBe('2026-W41')
  })

  it('weekOf 跨年周归到 ISO 年（2025-12-29 属于 2026-W01）', () => {
    expect(weekOf(new Date(2025, 11, 29))).toBe('2026-W01')
  })

  it('previousWeek 取上一周', () => {
    expect(previousWeek(new Date(2026, 9, 7))).toBe('2026-W40')
  })

  it('previousWeek 跨 ISO 年回退到上年末周', () => {
    expect(previousWeek(new Date(2026, 0, 1))).toBe('2025-W52')
  })

  it('periodLabel 周带日期区间，月原样，空值 -', () => {
    expect(periodLabel('2026-W41')).toBe('2026年第41周（10/05–10/11）')
    expect(periodLabel('2026-09')).toBe('2026-09')
    expect(periodLabel(null)).toBe('-')
  })

  it('weekStartDate 给该周周一，非周编码给 null', () => {
    expect(toDateString(weekStartDate('2026-W41') as Date)).toBe('2026-10-05')
    expect(toDateString(weekStartDate('2026-W01') as Date)).toBe('2025-12-29')
    expect(weekStartDate('2026-09')).toBeNull()
    expect(weekStartDate(null)).toBeNull()
  })

  it('toDateString / parseDateString 往返一致且用本地日期', () => {
    const d = new Date(2026, 9, 5)
    expect(toDateString(d)).toBe('2026-10-05')
    expect(toDateString(parseDateString('2026-10-05') as Date)).toBe('2026-10-05')
    expect(parseDateString('2026-10-05')?.getMonth()).toBe(9)
    expect(parseDateString('bad')).toBeNull()
    expect(parseDateString(null)).toBeNull()
  })

  it('weekOf 与 weekStartDate 互为逆（周一日期归到本 ISO 周）', () => {
    const monday = weekStartDate('2026-W41') as Date
    expect(weekOf(monday)).toBe('2026-W41')
  })
})
