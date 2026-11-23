import { describe, expect, it } from 'vitest'

import {
  EMPTY_OWNERSHIP,
  ownershipOf,
  ownershipView,
  READ_ONLY_TOOLTIP,
  SCOPE_OPTIONS,
} from '@/utils/ownership'

describe('ownershipOf', () => {
  it('后端未下发 ownership 字段时返回兜底值（不报错）', () => {
    expect(ownershipOf({})).toEqual(EMPTY_OWNERSHIP)
    expect(ownershipOf(undefined)).toEqual(EMPTY_OWNERSHIP)
    expect(ownershipOf(null)).toEqual(EMPTY_OWNERSHIP)
    expect(ownershipOf({ ownership: null })).toEqual(EMPTY_OWNERSHIP)
  })

  it('正常透传后端下发的字段', () => {
    const info = ownershipOf({
      ownership: { is_own: false, shared_by_roles: ['海油专线组'], read_only: true },
    })
    expect(info.is_own).toBe(false)
    expect(info.shared_by_roles).toEqual(['海油专线组'])
    expect(info.read_only).toBe(true)
  })

  it('内部字段缺失 / 类型不对时补齐，不抛异常', () => {
    const info = ownershipOf({
      ownership: { shared_by_roles: undefined } as never,
    })
    expect(info.shared_by_roles).toEqual([])
    expect(info.is_own).toBe(false)
    expect(info.read_only).toBe(false)
  })

  it('过滤掉空字符串的角色组名', () => {
    const info = ownershipOf({
      ownership: { is_own: false, shared_by_roles: ['A', '', 'B'], read_only: false },
    })
    expect(info.shared_by_roles).toEqual(['A', 'B'])
  })
})

describe('ownershipView', () => {
  it('自己的资源 -> isOwn，无角色组名', () => {
    const v = ownershipView({
      ownership: { is_own: true, shared_by_roles: [], read_only: false },
    })
    expect(v.isOwn).toBe(true)
    expect(v.shown).toEqual([])
    expect(v.others).toBe(false)
  })

  it('角色组共享来的 -> 展示角色组名', () => {
    const v = ownershipView({
      ownership: { is_own: false, shared_by_roles: ['海油专线组'], read_only: true },
    })
    expect(v.isOwn).toBe(false)
    expect(v.shown).toEqual(['海油专线组'])
    expect(v.rest).toBe(0)
    expect(v.readOnly).toBe(true)
    expect(v.others).toBe(false)
  })

  it('多个角色组 -> 折叠成 +N，并保留全量供 tooltip', () => {
    const v = ownershipView({
      ownership: { is_own: false, shared_by_roles: ['A', 'B', 'C', 'D'], read_only: true },
    })
    expect(v.shown).toEqual(['A', 'B'])
    expect(v.rest).toBe(2)
    expect(v.all).toEqual(['A', 'B', 'C', 'D'])
  })

  it('既不是自己的、也不在任何角色组里 -> others', () => {
    const v = ownershipView({
      ownership: { is_own: false, shared_by_roles: [], read_only: false },
    })
    expect(v.others).toBe(true)
    expect(v.shown).toEqual([])
  })

  it('字段缺失时按「自己的、不可只读」处理（旧行为）', () => {
    const v = ownershipView({})
    expect(v.isOwn).toBe(true)
    expect(v.readOnly).toBe(false)
  })

  it('字段缺失时可用 fallbackIsOwn 兜底（后端对 admin 不下发 ownership）', () => {
    const v = ownershipView({}, { fallbackIsOwn: false })
    expect(v.isOwn).toBe(false)
    expect(v.others).toBe(true)
    expect(v.readOnly).toBe(false)
  })

  it('limit 可调', () => {
    const v = ownershipView(
      { ownership: { is_own: false, shared_by_roles: ['A', 'B', 'C'], read_only: true } },
      { limit: 1 },
    )
    expect(v.shown).toEqual(['A'])
    expect(v.rest).toBe(2)
  })
})

describe('常量', () => {
  it('只读提示文案固定', () => {
    expect(READ_ONLY_TOOLTIP).toBe('角色组共享（只读），仅属主与管理员可修改')
  })

  it('范围筛选项为 全部 / 仅我的 / 角色组共享的', () => {
    expect(SCOPE_OPTIONS.map((o) => o.value)).toEqual(['all', 'mine', 'shared'])
    expect(SCOPE_OPTIONS.map((o) => o.label)).toEqual(['全部', '仅我的', '角色组共享的'])
  })
})
