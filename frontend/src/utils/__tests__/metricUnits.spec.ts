/**
 * 单位预置清单单测。
 *
 * 锁定三件事，都是「踩过坑」的语义：
 * 1. 只有基准单位带 `base`，带 K/M/G/T 前缀的单位一律不可换算（否则会出 `KKbps`）；
 * 2. `standardBaseOf` 命中预置 -> 标准进制，自定义/带前缀 -> null（必须手填进制）；
 * 3. `hasScalePrefix` 只认大写前缀，`bit/s`、`bps` 这类基准单位不能误判。
 *
 * 与 backend/tests/test_unit_scale.py 的分工：那边锁后端换算口径，这边只锁前端清单与判定。
 */
import { describe, expect, it } from 'vitest'

import {
  baseUnitSuggestion,
  EXAMPLE_RAW_VALUE,
  hasScalePrefix,
  isConvertibleUnit,
  METRIC_UNIT_GROUPS,
  standardBaseOf,
} from '@/utils/metricUnits'

describe('METRIC_UNIT_GROUPS', () => {
  it('分组与选项非空，value 全局唯一', () => {
    expect(METRIC_UNIT_GROUPS.length).toBeGreaterThan(0)
    const seen = new Set<string>()
    for (const group of METRIC_UNIT_GROUPS) {
      expect(group.group).not.toBe('')
      expect(group.options.length).toBeGreaterThan(0)
      for (const option of group.options) {
        expect(option.value).not.toBe('')
        expect(option.label).not.toBe('')
        expect(seen.has(option.value)).toBe(false)
        seen.add(option.value)
      }
    }
  })

  it('带前缀的单位不给 base（表示不可换算）', () => {
    for (const group of METRIC_UNIT_GROUPS) {
      for (const option of group.options) {
        if (hasScalePrefix(option.value)) expect(option.base).toBeUndefined()
        else expect([1000, 1024]).toContain(option.base)
      }
    }
  })

  it('速率组标准进制 1000、字节组标准进制 1024', () => {
    const rate = METRIC_UNIT_GROUPS.find((g) => g.group === '速率与带宽')
    const byte = METRIC_UNIT_GROUPS.find((g) => g.group === '字节与存储')
    expect(rate?.options.find((o) => o.value === 'bps')?.base).toBe(1000)
    expect(byte?.options.find((o) => o.value === 'B')?.base).toBe(1024)
  })
})

describe('standardBaseOf', () => {
  it('命中预置基准单位', () => {
    expect(standardBaseOf('bps')).toBe(1000)
    expect(standardBaseOf('B')).toBe(1024)
    expect(standardBaseOf('  bps  ')).toBe(1000)
  })

  it('带前缀单位与自定义单位 -> null（勾换算时必须手填进制）', () => {
    expect(standardBaseOf('Kbps')).toBeNull()
    expect(standardBaseOf('MB')).toBeNull()
    expect(standardBaseOf('个')).toBeNull()
    expect(standardBaseOf('')).toBeNull()
    expect(standardBaseOf(null)).toBeNull()
    expect(standardBaseOf(undefined)).toBeNull()
  })
})

describe('hasScalePrefix', () => {
  it('识别已带前缀的单位', () => {
    expect(hasScalePrefix('Kbps')).toBe(true)
    expect(hasScalePrefix('Mbps')).toBe(true)
    expect(hasScalePrefix('MB')).toBe(true)
    expect(hasScalePrefix('GiB')).toBe(true)
    expect(hasScalePrefix('TB')).toBe(true)
  })

  it('基准单位与中文单位不误判', () => {
    expect(hasScalePrefix('bps')).toBe(false)
    expect(hasScalePrefix('bit/s')).toBe(false)
    expect(hasScalePrefix('B')).toBe(false)
    expect(hasScalePrefix('个')).toBe(false)
    expect(hasScalePrefix('%')).toBe(false)
    expect(hasScalePrefix('')).toBe(false)
  })
})

describe('baseUnitSuggestion', () => {
  it('给带前缀单位推荐基准单位', () => {
    expect(baseUnitSuggestion('Kbps')).toBe('bps')
    expect(baseUnitSuggestion('Mbps')).toBe('bps')
    expect(baseUnitSuggestion('MB')).toBe('B')
    expect(baseUnitSuggestion('GiB')).toBe('B')
  })

  it('剥不出预置基准单位时不瞎推荐', () => {
    expect(baseUnitSuggestion('bps')).toBeNull()
    expect(baseUnitSuggestion('M个')).toBeNull()
  })
})

describe('isConvertibleUnit', () => {
  it('基准单位与自定义单位可换算，带前缀单位不可', () => {
    expect(isConvertibleUnit('bps')).toBe(true)
    expect(isConvertibleUnit('个')).toBe(true)
    expect(isConvertibleUnit('Kbps')).toBe(false)
    expect(isConvertibleUnit('')).toBe(false)
  })
})

describe('EXAMPLE_RAW_VALUE', () => {
  it('固定示例 5 位整数 + 3 位小数', () => {
    expect(EXAMPLE_RAW_VALUE).toBe(12345.678)
    expect(String(EXAMPLE_RAW_VALUE).split('.')[0]).toHaveLength(5)
    expect(String(EXAMPLE_RAW_VALUE).split('.')[1]).toHaveLength(3)
  })
})