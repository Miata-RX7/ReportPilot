/**
 * 单位换算前端镜像单测。
 *
 * 与后端 backend/tests/test_unit_scale.py 同参数、同期望：任何一侧改语义，两侧的
 * 测试会同时红掉，避免「报告是 262.47 Kbps、页面是 262468.90 bps」的口径分裂。
 */
import { describe, expect, it } from 'vitest'

import type { UnitScaleConfig } from '@/api/types'
import {
  autoOrder,
  DEFAULT_DISPLAY_DECIMALS,
  disabledUnitConfig,
  displayUnitOf,
  joinValueUnit,
  resolveScale,
  scaleValue,
} from '@/utils/unit'

const auto = (over: Partial<UnitScaleConfig> = {}): UnitScaleConfig => ({ mode: 'auto', ...over })
const fixed = (over: Partial<UnitScaleConfig> = {}): UnitScaleConfig => ({ mode: 'fixed', ...over })

describe('autoOrder', () => {
  it('量级分档（1000 进制）', () => {
    expect(autoOrder(0, 1000)).toBe(0)
    expect(autoOrder(999, 1000)).toBe(0)
    expect(autoOrder(1000, 1000)).toBe(1)
    expect(autoOrder(262468.9041666667, 1000)).toBe(1)
    expect(autoOrder(1_000_000, 1000)).toBe(2)
    // 浮点陷阱：Math.log(1e9)/Math.log(1000) 会算成 2
    expect(autoOrder(1e9, 1000)).toBe(3)
    expect(autoOrder(1e12, 1000)).toBe(4)
  })

  it('取绝对值（负值同档）', () => {
    expect(autoOrder(-1000, 1000)).toBe(1)
  })

  it('超大值封顶 E 档', () => {
    expect(autoOrder(1e21, 1000)).toBe(6)
  })

  it('非有限值退回 0 档', () => {
    expect(autoOrder(Number.POSITIVE_INFINITY, 1000)).toBe(0)
    expect(autoOrder(Number.NaN, 1000)).toBe(0)
  })

  it('1024 进制用二进制前缀档位', () => {
    expect(autoOrder(1023, 1024)).toBe(0)
    expect(autoOrder(2048, 1024)).toBe(1)
  })
})

describe('resolveScale', () => {
  it('未配置 / 非法模式 -> null（不换算）', () => {
    expect(resolveScale(null, { magnitude: 1 })).toBeNull()
    expect(resolveScale(undefined, { magnitude: 1 })).toBeNull()
    expect(resolveScale({ mode: 'none' }, { magnitude: 1 })).toBeNull()
  })

  it('auto：262468.90 bps -> 262.47 Kbps', () => {
    const scale = resolveScale(auto({ base: 1000, suffix_unit: 'bps' }), {
      magnitude: 262468.9041666667,
    })
    expect(scale).toMatchObject({ mode: 'auto', factor: 1000, unit: 'Kbps', precision: 2 })
    expect(scaleValue(262468.9041666667, scale)).toBeCloseTo(262.4689041667, 6)
  })

  it('auto：后缀为空时回退指标原始单位', () => {
    const scale = resolveScale(auto(), { magnitude: 2_000_000, baseUnit: 'bps' })
    expect(scale?.unit).toBe('Mbps')
  })

  it('auto：1024 进制拼二进制前缀', () => {
    const scale = resolveScale(auto({ base: 1024, suffix_unit: 'B' }), { magnitude: 2048 })
    expect(scale?.factor).toBe(1024)
    expect(scale?.unit).toBe('KiB')
  })

  it('auto：0 值保持原单位（0 档）', () => {
    const scale = resolveScale(auto({ suffix_unit: 'bps' }), { magnitude: 0 })
    expect(scale).toMatchObject({ factor: 1, unit: 'bps' })
  })

  it('fixed：自定义除数 + 目标单位', () => {
    const scale = resolveScale(fixed({ divisor: 1000, display_unit: 'Kbps' }), { magnitude: 1 })
    expect(scale).toMatchObject({ mode: 'fixed', factor: 1000, unit: 'Kbps' })
  })

  it('fixed：除数为 0 / 负数 / 非数字 -> null', () => {
    expect(resolveScale(fixed({ divisor: 0 }), { magnitude: 1 })).toBeNull()
    expect(resolveScale(fixed({ divisor: -1 }), { magnitude: 1 })).toBeNull()
    expect(resolveScale(fixed({ divisor: Number.NaN }), { magnitude: 1 })).toBeNull()
  })

  it('fixed：无目标单位时回退原始单位', () => {
    expect(resolveScale(fixed({ divisor: 60 }), { magnitude: 1, baseUnit: 's' })?.unit).toBe('s')
  })

  it('展示精度优先级：配置 decimals > display_precision > 默认 2 位', () => {
    expect(resolveScale(auto(), { magnitude: 1 })?.precision).toBe(DEFAULT_DISPLAY_DECIMALS)
    expect(resolveScale(auto(), { magnitude: 1, precision: 4 })?.precision).toBe(4)
    expect(resolveScale(auto({ decimals: 3 }), { magnitude: 1, precision: 4 })?.precision).toBe(3)
  })
})

describe('scaleValue / displayUnitOf', () => {
  it('scale 为 null 时原样返回', () => {
    expect(scaleValue(5, null)).toBe(5)
    expect(scaleValue(null, null)).toBeNull()
  })

  it('显示单位：换算后为空则回退原始单位', () => {
    expect(displayUnitOf(null, 'bps')).toBe('bps')
    expect(displayUnitOf(resolveScale(fixed({ divisor: 1000 }), { magnitude: 1 }), 'bps')).toBe('bps')
    expect(
      displayUnitOf(resolveScale(auto({ suffix_unit: 'bps' }), { magnitude: 1000 }), 'bps'),
    ).toBe('Kbps')
  })
})

describe('joinValueUnit', () => {
  it('符号类单位紧贴数值', () => {
    expect(joinValueUnit('95.20', '%')).toBe('95.20%')
    expect(joinValueUnit('30', '°C')).toBe('30°C')
  })

  it('普通单位空格分隔', () => {
    expect(joinValueUnit('262.47', 'Kbps')).toBe('262.47 Kbps')
  })

  it('空单位只返回值', () => {
    expect(joinValueUnit('262.47', '')).toBe('262.47')
    expect(joinValueUnit('262.47', null)).toBe('262.47')
    expect(joinValueUnit('262.47', undefined)).toBe('262.47')
  })
})

describe('disabledUnitConfig', () => {
  it('关闭换算时提交 none', () => {
    expect(disabledUnitConfig()).toEqual({ mode: 'none' })
  })
})