/**
 * AIOps 定位串与能力判定表的前端单测。
 *
 * **这些用例与 `backend/tests/test_aiops_keys.py` 一一对应** —— 前后端各有一份
 * 实现，任一侧改了编码格式或判定表而另一侧没跟上，就会出现「页面能选、保存后
 * 取不到数」这种极难定位的问题，所以两边必须同时改、同时跑。
 */
import { describe, expect, it } from 'vitest'

import {
  type AiopsColumn,
  allowedMetricTypes,
  columnLabel,
  decodeRef,
  encodeRef,
  isAiopsRef,
  isListRef,
  metricTypeBlockedReason,
  refLabel,
  supportsCurve,
} from '@/utils/aiopsRef'

describe('encodeRef / decodeRef', () => {
  it('number / text 监控项往返', () => {
    const ref = encodeRef(715, 1694)
    expect(ref).toBe('n:715:1694')
    const got = decodeRef(ref!)
    expect(got).toEqual({ instanceId: 715, itemId: 1694, columnId: null, rowKey: null })
    expect(isListRef(got)).toBe(false)
  })

  it('list 某行某列往返', () => {
    const ref = encodeRef(715, 1696, 11504, 'GigabitEthernet1/0/1')
    const got = decodeRef(ref!)
    expect(got).toEqual({
      instanceId: 715,
      itemId: 1696,
      columnId: 11504,
      rowKey: 'GigabitEthernet1/0/1',
    })
    expect(isListRef(got)).toBe(true)
  })

  // 主键行含 / : 空格括号中文都能往返 —— 这是用 base64url 的全部理由
  it.each([
    ['GigabitEthernet1/0/1'],
    ['Ethernet-Trunk1'],
    ['Vlanif30'],
    ['Console9/0/0'],
    ['CNOOC-(HK-Internet_Center)'],
    ['端口 1'],
    ['a:b:c'],
    ['中文行键'],
  ])('主键行 %s 往返无损', (rowKey) => {
    expect(decodeRef(encodeRef(1, 2, 3, rowKey)!)!.rowKey).toBe(rowKey)
  })

  it('columnId 与 rowKey 必须成对，否则返回 null', () => {
    // 半截坐标会让行筛选静默失效、返回跨行聚合值（实测踩过），必须拦
    expect(encodeRef(1, 2, 3)).toBeNull()
    expect(encodeRef(1, 2, undefined, 'Port1')).toBeNull()
  })

  it.each([[''], ['x:1:2'], ['l:1:2'], ['l:1:2:3'], ['n:1'], ['1:2'], ['l:a:b:c:d']])(
    '非法定位串 %j 解析为 null',
    (bad) => {
      expect(decodeRef(bad)).toBeNull()
      expect(isAiopsRef(bad)).toBe(false)
    },
  )

  it('纯数字的 Zabbix item_id 不被误判成 AIOps 定位串', () => {
    expect(isAiopsRef('42253')).toBe(false)
    expect(decodeRef('42253')).toBeNull()
  })

  it('编码稳定：同坐标反复编码逐字相同（反查依赖这个）', () => {
    expect(encodeRef(715, 1696, 11504, 'Port1')).toBe(encodeRef('715', '1696', '11504', 'Port1'))
  })
})

describe('allowedMetricTypes 判定表', () => {
  const col = (value_type: 'number' | 'text', has_history: boolean): AiopsColumn => ({
    column_id: 1,
    name: 'c',
    unit: '',
    value_type,
    has_history,
    is_primary: false,
  })

  it('number 监控项 -> scalar / curve', () => {
    expect(allowedMetricTypes('number')).toEqual(['scalar', 'curve'])
    expect(supportsCurve('number')).toBe(true)
  })

  it('text 监控项 -> text', () => {
    expect(allowedMetricTypes('text')).toEqual(['text'])
    expect(supportsCurve('text')).toBe(false)
  })

  it('list 的 text 列 -> 只有 text（即使有历史）', () => {
    // 实测存在 text + source_history=1 的列（如「状态」）
    expect(allowedMetricTypes('list', col('text', true))).toEqual(['text'])
    expect(allowedMetricTypes('list', col('text', false))).toEqual(['text'])
  })

  it('list 的 number 列 + 有历史 -> scalar / curve', () => {
    expect(allowedMetricTypes('list', col('number', true))).toEqual(['scalar', 'curve'])
    expect(supportsCurve('list', col('number', true))).toBe(true)
  })

  it('list 的 number 列 + 无历史 -> 只有 scalar（用户明确要求）', () => {
    // 实测存在 number + source_history=0 的列（如「带宽」，是配置值不是采集值）
    expect(allowedMetricTypes('list', col('number', false))).toEqual(['scalar'])
    expect(supportsCurve('list', col('number', false))).toBe(false)
  })

  it('list 未选列时无可选项', () => {
    expect(allowedMetricTypes('list', null)).toEqual([])
    expect(allowedMetricTypes('list')).toEqual([])
  })

  it('pie 对 AIOps 一律不可用', () => {
    expect(allowedMetricTypes('number')).not.toContain('pie')
    expect(allowedMetricTypes('text')).not.toContain('pie')
    expect(allowedMetricTypes('list', col('number', true))).not.toContain('pie')
  })

  it('数据源类型缺失时不做收窄（由数据源级别另行拦截）', () => {
    expect(allowedMetricTypes(null)).toEqual([])
  })
})

describe('metricTypeBlockedReason（禁用原因文案）', () => {
  const col = (value_type: 'number' | 'text', has_history: boolean): AiopsColumn => ({
    column_id: 1,
    name: 'c',
    unit: '',
    value_type,
    has_history,
    is_primary: false,
  })

  it('pie 一律被拒，且理由提到 up/down 值域语义', () => {
    expect(metricTypeBlockedReason('pie', 'number')).toContain('UP/DOWN')
    expect(metricTypeBlockedReason('pie', 'list', col('number', true))).toContain('UP/DOWN')
  })

  it('无历史列禁曲线时给出可操作建议', () => {
    const why = metricTypeBlockedReason('curve', 'list', col('number', false))
    expect(why).toContain('source_history=0')
    expect(why).toContain('数值')
  })

  it('text 列禁数值/曲线', () => {
    expect(metricTypeBlockedReason('scalar', 'list', col('text', true))).toContain('文本类型')
    expect(metricTypeBlockedReason('curve', 'list', col('text', true))).toContain('文本类型')
  })

  it('数值列禁文本', () => {
    expect(metricTypeBlockedReason('text', 'list', col('number', true))).toContain('数值类型')
  })

  it('允许的类型返回 null（表示没被禁用）', () => {
    expect(metricTypeBlockedReason('curve', 'list', col('number', true))).toBeNull()
    expect(metricTypeBlockedReason('scalar', 'number')).toBeNull()
    expect(metricTypeBlockedReason('text', 'text')).toBeNull()
  })
})

describe('展示辅助', () => {
  it('列名带单位', () => {
    expect(
      columnLabel({
        column_id: 11504,
        name: '入流量',
        unit: 'auto1000 bps',
        value_type: 'number',
        has_history: true,
        is_primary: false,
      }),
    ).toBe('入流量 (auto1000 bps)')
    expect(
      columnLabel({
        column_id: 11498,
        name: '端口名称',
        unit: '',
        value_type: 'text',
        has_history: false,
        is_primary: true,
      }),
    ).toBe('端口名称')
  })

  it('定位串展示成 监控项/列/行', () => {
    // 非list（number / text 监控项）：只给监控项名
    const plain = decodeRef(encodeRef(715, 10143)!)!
    expect(refLabel(plain, 'CPU使用率')).toBe('CPU使用率')
    expect(refLabel(plain)).toBe('item-10143')

    // list 列：带列名与主键行
    const ref = decodeRef(encodeRef(715, 9669, 11504, 'Port1')!)!
    expect(
      refLabel(
        ref,
        '端口表',
        {
          column_id: 11504,
          name: '入流量',
          unit: 'bps',
          value_type: 'number',
          has_history: true,
          is_primary: false,
        },
      ),
    ).toBe('端口表 / 入流量 (bps) / Port1')
    // 列元数据还没加载出来时，至少能给出监控项与行
    expect(refLabel(ref, '端口表')).toBe('端口表 / Port1')
  })
})