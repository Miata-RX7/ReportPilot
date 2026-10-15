import { describe, expect, it } from 'vitest'

import type { CalcFormula, QueryConfig } from '@/api/types'
import {
  changePctClass,
  factDisplayNumber,
  factDisplayUnit,
  factNumber,
  factShape,
  factValueText,
  foldTags,
  formatChangePct,
  formatCompare,
  formatFactValue,
  formatScope,
  METRIC_TYPE_LABEL,
  metricTypeClass,
  scopeNamesFrom,
} from '@/utils/metric'

const baseCalc: CalcFormula = { agg: 'avg', compare: 'none', baseline_lookback: null }

describe('formatScope', () => {
  const qc: QueryConfig = {
    host_group_ids: ['174'],
    host_ids: ['12793'],
    item_ids: ['261006', '261108'],
  }

  it('有名称时展示 主机组 / 主机 / 监控项 名称', () => {
    const scope = formatScope(qc, {
      host_groups: { '174': '爱迅达办公室' },
      hosts: { '12793': '测试华为S5735' },
      items: { '261006': 'Bits received', '261108': 'Bits sent' },
    })
    expect(scope).toBe('爱迅达办公室 / 测试华为S5735 / Bits received、Bits sent')
  })

  it('无名称解析时降级为 id', () => {
    expect(formatScope(qc)).toBe('174 / 12793 / 261006、261108')
  })

  it('部分名称缺失时混合展示', () => {
    const scope = formatScope(qc, { host_groups: { '174': '爱迅达办公室' } })
    expect(scope).toBe('爱迅达办公室 / 12793 / 261006、261108')
  })

  it('多主机用、分隔；监控项超过 2 个折叠计数', () => {
    const scope = formatScope(
      { host_group_ids: ['1'], host_ids: ['a', 'b'], item_ids: ['i1', 'i2', 'i3', 'i4'] },
      { hosts: { a: 'web-01', b: 'web-02' } },
    )
    expect(scope).toBe('1 / web-01、web-02 / i1、i2 等 4 项')
  })

  it('全空时显示占位符', () => {
    expect(formatScope({ host_group_ids: [], host_ids: [], item_ids: [] })).toBe('—')
  })
})

describe('scopeNamesFrom', () => {
  it('由详情解析结果生成映射', () => {
    const names = scopeNamesFrom({
      host_groups: [{ id: '174', name: '爱迅达办公室' }],
      hosts: [{ id: '12793', name: '测试华为S5735' }],
      items: [{ id: '261006', name: 'Bits received', host_id: '12793', host: '测试华为S5735' }],
    })
    expect(names.host_groups?.['174']).toBe('爱迅达办公室')
    expect(names.hosts?.['12793']).toBe('测试华为S5735')
    expect(names.items?.['261006']).toBe('Bits received')
  })

  it('null / undefined 返回空映射', () => {
    expect(scopeNamesFrom(null)).toEqual({})
    expect(scopeNamesFrom(undefined)).toEqual({})
  })
})

describe('foldTags', () => {
  it('不超过 limit 时全部展示', () => {
    expect(foldTags(['a', 'b', 'c'])).toEqual({ shown: ['a', 'b', 'c'], rest: 0 })
  })

  it('超过 limit 时折叠为 +N', () => {
    expect(foldTags(['a', 'b', 'c', 'd', 'e'])).toEqual({ shown: ['a', 'b', 'c'], rest: 2 })
  })

  it('空数组', () => {
    expect(foldTags([])).toEqual({ shown: [], rest: 0 })
  })

  it('自定义 limit', () => {
    expect(foldTags(['a', 'b'], 1)).toEqual({ shown: ['a'], rest: 1 })
  })
})

describe('formatCompare', () => {
  it('none 只显示 agg', () => {
    expect(formatCompare(baseCalc)).toBe('avg')
  })

  it('mom 带环比与回溯', () => {
    expect(formatCompare({ ...baseCalc, compare: 'mom', baseline_lookback: 1 })).toBe(
      'avg · 环比 mom(1)',
    )
  })

  it('yoy 带同比与回溯', () => {
    expect(formatCompare({ ...baseCalc, compare: 'yoy', baseline_lookback: 12 })).toBe(
      'avg · 同比 yoy(12)',
    )
  })
})

describe('changePctClass', () => {
  it('大涨 warning / 大跌 success / 平稳 neutral / null neutral', () => {
    expect(changePctClass(26.59)).toBe('warning')
    expect(changePctClass(-26.59)).toBe('success')
    expect(changePctClass(5)).toBe('neutral')
    expect(changePctClass(-5)).toBe('neutral')
    expect(changePctClass(null)).toBe('neutral')
  })

  it('阈值可自定义', () => {
    expect(changePctClass(8, 5)).toBe('warning')
  })
})

describe('formatChangePct', () => {
  it('正数带 +，负数不带，null 为 —', () => {
    expect(formatChangePct(26.5927)).toBe('+26.59%')
    expect(formatChangePct(-1.9245)).toBe('-1.92%')
    expect(formatChangePct(null)).toBe('—')
  })
})

describe('formatFactValue', () => {
  it('precision 为 null 时保留原始值', () => {
    expect(formatFactValue(93196.30275487003, null)).toBe('93196.30275487003')
  })

  it('precision 为整数时固定小数位', () => {
    expect(formatFactValue(93196.30275487003, 2)).toBe('93196.30')
    expect(formatFactValue(210, 0)).toBe('210')
    expect(formatFactValue(1.5, 4)).toBe('1.5000')
  })
})


describe('formatScope 按指标类型分型', () => {
  const names = { host_groups: { '1': '组A' }, hosts: { '10': 'web-01' } }

  it('scalar：组 / 主机 / 监控项', () => {
    const qc = { host_group_ids: ['1'], host_ids: ['10'], item_ids: ['100', '101'] }
    expect(formatScope(qc, names, 'scalar')).toBe('组A / web-01 / 100、101')
  })

  it('curve：显示曲线条数与展示名', () => {
    const qc = {
      host_group_ids: ['1'],
      host_ids: ['10'],
      item_ids: [],
      series: [
        { item_id: '100', display_name: '入向流量' },
        { item_id: '101', display_name: '出向流量' },
      ],
    }
    expect(formatScope(qc, names, 'curve')).toBe('组A / web-01 / 入向流量、出向流量（2 条曲线）')
  })

  it('curve：超过 2 条折叠计数', () => {
    const qc = {
      host_group_ids: ['1'],
      host_ids: ['10'],
      item_ids: [],
      series: [
        { item_id: '1', display_name: 'a' },
        { item_id: '2', display_name: 'b' },
        { item_id: '3', display_name: 'c' },
      ],
    }
    expect(formatScope(qc, names, 'curve')).toBe('组A / web-01 / a、b 等 3 条曲线')
  })

  it('pie：只显示单个监控项', () => {
    const qc = { host_group_ids: ['1'], host_ids: ['10'], item_ids: ['100'] }
    expect(formatScope(qc, { ...names, items: { '100': 'ICMP ping' } }, 'pie')).toBe(
      '组A / web-01 / ICMP ping',
    )
  })

  it('text fixed：item_ids 为空时只显示范围', () => {
    const qc = { host_group_ids: ['1'], host_ids: ['10'], item_ids: [] }
    expect(formatScope(qc, names, 'text')).toBe('组A / web-01')
  })

  it('默认按 scalar 处理（缺省参数）', () => {
    const qc = { host_group_ids: ['1'], host_ids: ['10'], item_ids: ['100'] }
    expect(formatScope(qc, names)).toBe('组A / web-01 / 100')
  })
})

describe('metricTypeClass / METRIC_TYPE_LABEL', () => {
  it('四类标签与配色', () => {
    expect(METRIC_TYPE_LABEL.scalar).toBe('数值')
    expect(METRIC_TYPE_LABEL.curve).toBe('曲线图')
    expect(METRIC_TYPE_LABEL.pie).toBe('饼图')
    expect(METRIC_TYPE_LABEL.text).toBe('文本')
    expect(metricTypeClass('scalar')).toBe('info')
    expect(metricTypeClass('curve')).toBe('success')
    expect(metricTypeClass('pie')).toBe('warning')
    expect(metricTypeClass('text')).toBe('neutral')
  })
})

describe('factShape / factNumber', () => {
  it('历史数据缺 value_shape 时按 scalar', () => {
    expect(factShape({ value_shape: undefined })).toBe('scalar')
    expect(factShape({ value_shape: 'text' })).toBe('text')
  })

  it('只有标量形态才返回数值', () => {
    expect(factNumber({ value: 12.5 })).toBe(12.5)
    expect(factNumber({ value: { text: 'x' }, value_shape: 'text' })).toBeNull()
    expect(factNumber({ value: { series: [] }, value_shape: 'series' })).toBeNull()
  })
})

describe('factValueText', () => {
  it('scalar：按 display_precision 格式化', () => {
    expect(factValueText({ value: 1.23456, display_precision: 2 })).toBe('1.23')
    expect(factValueText({ value: 1.23456, display_precision: null })).toBe('1.23456')
  })

  it('text：取 payload.text', () => {
    expect(factValueText({ value: { text: '本月无异常' }, value_shape: 'text' })).toBe('本月无异常')
    expect(factValueText({ value: {}, value_shape: 'text' })).toBe('—')
  })

  it('pie：UP/DOWN 占比', () => {
    const v = factValueText({
      value: { up_count: 3, down_count: 1, up_pct: 75, down_pct: 25 },
      value_shape: 'pie',
    })
    expect(v).toBe('UP 75.0% / DOWN 25.0%')
  })

  it('series：曲线/点数量摘要（阶段 1 不渲染图表）', () => {
    const v = factValueText({
      value: {
        series: [
          { name: 'in', points: [[1, 2], [2, 3]] },
          { name: 'out', points: [[1, 5]] },
        ],
      },
      value_shape: 'series',
    })
    expect(v).toBe('2 条曲线 / 3 个点')
  })

  it('scalar 但值为 dict 时安全降级', () => {
    expect(factValueText({ value: { oops: 1 } })).toBe('—')
  })
})


describe('展示口径（单位换算）优先 / 回退', () => {
  it('factDisplayNumber 优先 display_value', () => {
    expect(
      factDisplayNumber({ value: 262468.9041666667, display_value: 262.4689041666667 }),
    ).toBeCloseTo(262.4689041667, 6)
  })

  it('缺 display_value 时回退原始 value（旧报告快照）', () => {
    expect(factDisplayNumber({ value: 35 })).toBe(35)
    expect(factDisplayNumber({ value: 262468.9041666667, display_value: null })).toBe(
      262468.9041666667,
    )
  })

  it('非标量形态不参与展示数值', () => {
    expect(factDisplayNumber({ value: { text: 'x' }, value_shape: 'text' })).toBeNull()
  })

  it('factDisplayUnit 优先 display_unit，空串回退 unit', () => {
    expect(factDisplayUnit({ unit: 'bps', display_unit: 'Kbps' })).toBe('Kbps')
    expect(factDisplayUnit({ unit: 'bps', display_unit: '' })).toBe('bps')
    expect(factDisplayUnit({ unit: 'bps', display_unit: null })).toBe('bps')
    expect(factDisplayUnit({ unit: '%' })).toBe('%')
  })

  it('factValueText 用展示值 + 展示精度', () => {
    expect(
      factValueText({
        value: 262468.9041666667,
        display_value: 262.4689041666667,
        display_precision: 2,
      }),
    ).toBe('262.47')
  })
})


describe('历史 facts 兼容（无 value_shape）', () => {
  it('缺 value_shape 时按 scalar 处理且不崩', () => {
    const legacy = { value: 35, unit: '%', display_precision: null }
    expect(factShape(legacy)).toBe('scalar')
    expect(factNumber(legacy)).toBe(35)
    expect(factValueText(legacy)).toBe('35')
  })

  it('带 display_precision 的历史对象按标量格式化', () => {
    expect(factValueText({ value: 35.987, display_precision: 2 })).toBe('35.99')
  })

  it('旧报告里若出现结构化 value 也不崩（降级为 —）', () => {
    expect(factValueText({ value: { text: 'x' } })).toBe('—')
  })
})
