import MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// element-plus 依赖 DOM，测试中替换为纯 vi.fn（vi.hoisted 规避 mock 提升问题）
const { errorToast } = vi.hoisted(() => ({ errorToast: vi.fn() }))
vi.mock('element-plus', () => ({
  ElMessage: { error: errorToast, success: vi.fn() },
}))

import { http, setUnauthorizedHandler } from '@/api/http'
import { clearToken, getToken, setToken } from '@/utils/storage'

const mock = new MockAdapter(http)
const unauthorized = vi.fn()

describe('http 拦截器', () => {
  beforeEach(() => {
    mock.reset()
    errorToast.mockClear()
    unauthorized.mockClear()
    setUnauthorizedHandler(unauthorized)
    clearToken()
  })

  afterEach(() => {
    clearToken()
  })

  it('有 token 时注入 Authorization 头', async () => {
    setToken('test-jwt')
    mock.onGet('/auth/me').reply(200, { id: 1 })
    await http.get('/auth/me')
    expect(mock.history.get[0].headers?.Authorization).toBe('Bearer test-jwt')
  })

  it('无 token 时不注入 Authorization 头', async () => {
    mock.onGet('/auth/me').reply(200, { id: 1 })
    await http.get('/auth/me')
    expect(mock.history.get[0].headers?.Authorization).toBeUndefined()
  })

  it('401 清 token 并触发跳转处理', async () => {
    setToken('expired-jwt')
    mock.onGet('/datasources').reply(401, { detail: 'token 已过期' })
    await expect(http.get('/datasources')).rejects.toBeTruthy()
    expect(getToken()).toBe('')
    expect(unauthorized).toHaveBeenCalledTimes(1)
  })

  it('登录接口 401 不触发跳转（密码错误由页面展示）', async () => {
    mock.onPost('/auth/login').reply(401, { detail: '用户名或密码错误' })
    await expect(http.post('/auth/login', {}, { silent: true })).rejects.toBeTruthy()
    expect(unauthorized).not.toHaveBeenCalled()
  })

  it('403 提示无权操作且不清 token', async () => {
    setToken('user-jwt')
    mock.onPost('/datasources').reply(403, { detail: '需要管理员权限' })
    await expect(http.post('/datasources', {})).rejects.toBeTruthy()
    expect(errorToast).toHaveBeenCalledWith('无权操作（需要管理员权限）')
    expect(getToken()).toBe('user-jwt')
  })

  it('422 不弹全局提示（交由业务层）', async () => {
    mock.onPost('/metrics').reply(422, { detail: '必须指定 item_ids 或 item_key_pattern' })
    await expect(http.post('/metrics', {})).rejects.toBeTruthy()
    expect(errorToast).not.toHaveBeenCalled()
  })

  it('5xx 提示服务端错误', async () => {
    mock.onGet('/reports').reply(500, { detail: 'boom' })
    await expect(http.get('/reports')).rejects.toBeTruthy()
    expect(errorToast).toHaveBeenCalledWith('服务端错误，请查看后端日志')
  })

  it('silent 模式不弹提示', async () => {
    mock.onPost('/datasources').reply(403, { detail: '需要管理员权限' })
    await expect(http.post('/datasources', {}, { silent: true })).rejects.toBeTruthy()
    expect(errorToast).not.toHaveBeenCalled()
  })

  it('超时错误提示超时文案', async () => {
    mock.onPost('/reports').timeout()
    await expect(http.post('/reports', {})).rejects.toBeTruthy()
    expect(errorToast).toHaveBeenCalledWith('请求超时，请稍后重试')
  })
})
