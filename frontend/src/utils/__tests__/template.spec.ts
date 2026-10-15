import { describe, expect, it } from 'vitest'

import {
  MIXED_PLACEHOLDER_HINT,
  bindingProgressClass,
  bindingProgressText,
  bindingStats,
  canBindChartMetric,
  canEdit,
  canManage,
  isChartMetric,
  permissionClass,
  permissionText,
} from '@/utils/template'
import type { BindingRead } from '@/api/types'

describe('isChartMetric', () => {
  it('仅 curve / pie 为图表类型', () => {
    expect(isChartMetric('curve')).toBe(true)
    expect(isChartMetric('pie')).toBe(true)
    expect(isChartMetric('scalar')).toBe(false)
    expect(isChartMetric('text')).toBe(false)
    expect(isChartMetric(undefined)).toBe(false)
    expect(isChartMetric(null)).toBe(false)
  })
})

describe('canBindChartMetric（独占段落约束）', () => {
  it('非图表类型不受独占限制', () => {
    expect(canBindChartMetric('scalar', true)).toBe(true)
    expect(canBindChartMetric('scalar', false)).toBe(true)
    expect(canBindChartMetric('text', false)).toBe(true)
  })

  it('图表类型必须独占段落', () => {
    expect(canBindChartMetric('curve', true)).toBe(true)
    expect(canBindChartMetric('curve', false)).toBe(false)
    expect(canBindChartMetric('pie', true)).toBe(true)
    expect(canBindChartMetric('pie', false)).toBe(false)
  })

  it('缺失类型视为非图表（不阻塞）', () => {
    expect(canBindChartMetric(undefined, false)).toBe(true)
  })
})

describe('MIXED_PLACEHOLDER_HINT', () => {
  it('文案包含"混排"与"无法绑定"', () => {
    expect(MIXED_PLACEHOLDER_HINT).toContain('混排')
    expect(MIXED_PLACEHOLDER_HINT).toContain('无法绑定')
  })
})

describe('bindingStats', () => {
  const b = (placeholder: string): BindingRead => ({
    id: 1,
    placeholder,
    binding_type: 'metric',
    binding_ref_id: 1,
    binding_key: null,
    ai_config: null,
  })

  it('统计总数/已绑定/未绑定', () => {
    expect(bindingStats(['a', 'b', 'c'], [b('a'), b('b')])).toEqual({
      total: 3,
      bound: 2,
      unbound: ['c'],
    })
  })
})

describe('bindingProgressText / Class', () => {
  it('文案', () => {
    expect(bindingProgressText({ total: 3, bound: 3, unbound: [] })).toBe('已全部绑定')
    expect(bindingProgressText({ total: 3, bound: 1, unbound: ['a', 'b'] })).toBe('1/3 已绑定')
    expect(bindingProgressText({ total: 3, bound: 0, unbound: ['a', 'b', 'c'] })).toBe('未绑定')
    expect(bindingProgressText({ total: 0, bound: 0, unbound: [] })).toBe('无变量')
  })

  it('进度色', () => {
    expect(bindingProgressClass({ total: 2, bound: 2, unbound: [] })).toBe('success')
    expect(bindingProgressClass({ total: 2, bound: 1, unbound: ['x'] })).toBe('warning')
  })
})

describe('canEdit / canManage', () => {
  it('edit 与 manage 可编辑；view/undefined/null 不可', () => {
    expect(canEdit('manage')).toBe(true)
    expect(canEdit('edit')).toBe(true)
    expect(canEdit('view')).toBe(false)
    expect(canEdit(undefined)).toBe(false)
    expect(canEdit(null)).toBe(false)
  })

  it('仅 manage 可管理', () => {
    expect(canManage('manage')).toBe(true)
    expect(canManage('edit')).toBe(false)
    expect(canManage('view')).toBe(false)
  })
})

describe('permissionText / permissionClass', () => {
  it('文案与软标签色', () => {
    expect(permissionText('manage')).toBe('管理')
    expect(permissionText('edit')).toBe('编辑')
    expect(permissionText('view')).toBe('只读')
    expect(permissionClass('manage')).toBe('success')
    expect(permissionClass('edit')).toBe('info')
    expect(permissionClass('view')).toBe('neutral')
  })
})
