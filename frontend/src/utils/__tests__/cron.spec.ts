import { describe, expect, it } from 'vitest'

import { CRON_PRESETS, cronSummary, isValidCron, nextRunPreview } from '@/utils/cron'

describe('isValidCron', () => {
  it('接受 5 段式常规写法', () => {
    expect(isValidCron('0 9 1 * *')).toBe(true)
    expect(isValidCron('0 18 * * 1')).toBe(true)
    expect(isValidCron('*/5 * * * *')).toBe(true)
    expect(isValidCron('0 9 1,15 * *')).toBe(true)
    expect(isValidCron('0 9-18 * * *')).toBe(true)
  })

  it('拒绝非法表达式', () => {
    expect(isValidCron('')).toBe(false)
    expect(isValidCron('bad cron')).toBe(false)
    expect(isValidCron('0 9 1 *')).toBe(false) // 4 段
    expect(isValidCron('0 9 1 * * *')).toBe(false) // 6 段
    expect(isValidCron('99 9 1 * *')).toBe(false) // 分钟越界
    expect(isValidCron('0 25 1 * *')).toBe(false) // 小时越界
    expect(isValidCron('0 9 32 * *')).toBe(false) // 日越界
    expect(isValidCron('0 9 1 13 *')).toBe(false) // 月越界
  })
})

describe('cronSummary', () => {
  it('每月 1 号', () => {
    expect(cronSummary('0 9 1 * *')).toBe('1 号 09:00')
  })

  it('每月多天', () => {
    expect(cronSummary('0 9 1,15 * *')).toBe('1 号、15 号 09:00')
  })

  it('每周一', () => {
    expect(cronSummary('0 9 * * 1')).toBe('每周一 09:00')
  })

  it('周 0/7 都表示周日', () => {
    expect(cronSummary('0 18 * * 0')).toBe('每周日 18:00')
    expect(cronSummary('0 18 * * 7')).toBe('每周日 18:00')
  })

  it('每天', () => {
    expect(cronSummary('30 8 * * *')).toBe('每天 08:30')
  })

  it('带月份限定', () => {
    expect(cronSummary('0 9 1 1 *')).toBe('1 月 1 号 09:00')
  })
})

describe('nextRunPreview', () => {
  it('每月 1 号 09:00：给定 10 月中 -> 11 月 1 日', () => {
    const from = new Date(2026, 9, 15, 10, 30) // 2026-10-15 10:30
    expect(nextRunPreview('0 9 1 * *', from)).toBe('2026-11-01 09:00')
  })

  it('已过当天则滚到下个月', () => {
    const from = new Date(2026, 9, 1, 10, 0) // 已过 09:00
    expect(nextRunPreview('0 9 1 * *', from)).toBe('2026-11-01 09:00')
  })

  it('恰好在触发时刻之前 -> 当天', () => {
    const from = new Date(2026, 9, 1, 8, 59)
    expect(nextRunPreview('0 9 1 * *', from)).toBe('2026-10-01 09:00')
  })

  it('每周一', () => {
    const from = new Date(2026, 9, 9, 10, 0) // 周五
    expect(nextRunPreview('0 9 * * 1', from)).toBe('2026-10-12 09:00')
  })

  it('非法表达式给出提示', () => {
    expect(nextRunPreview('bad')).toBe('表达式无效')
  })
})

describe('CRON_PRESETS', () => {
  it('全部是合法 5 段式', () => {
    expect(CRON_PRESETS.length).toBeGreaterThan(0)
    for (const p of CRON_PRESETS) {
      expect(isValidCron(p.value)).toBe(true)
    }
  })
})