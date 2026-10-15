import { beforeEach, describe, expect, it } from 'vitest'

import { clearToken, getToken, setToken } from '@/utils/storage'

describe('token storage', () => {
  beforeEach(() => {
    clearToken()
  })

  it('默认无 token 返回空串', () => {
    expect(getToken()).toBe('')
  })

  it('写入后可读、清除后为空', () => {
    setToken('abc.def.ghi')
    expect(getToken()).toBe('abc.def.ghi')
    clearToken()
    expect(getToken()).toBe('')
  })
})
