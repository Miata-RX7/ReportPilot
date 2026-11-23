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

/**
 * 数组 query 参数的序列化（2026-11-20）。
 *
 * axios 1.x 默认把数组变成 `tag[]=a&tag[]=b`，而 FastAPI 的
 * `tag: list[str] = Query()` 只认**重复 key**（`tag=a&tag=b`）
 * -> 后端收到空列表 -> 筛选静默失效（不报错，只是「选了标签列表没变化」）。
 *
 * 这条用例就是防那个回归：全局 `paramsSerializer: { indexes: null }`
 * 被误删时，本用例会红。
 */
describe('数组 query 参数序列化', () => {
  it('数组序列化为重复 key，而不是 tag[]=', () => {
    const url = http.getUri({
      url: '/metrics/paged',
      params: { page: 1, tag: ['蒙古国航', '海油'] },
    })
    expect(url).toContain('tag=%E8%92%99%E5%8F%A4%E5%9B%BD%E8%88%AA')
    expect(url).toContain('tag=%E6%B5%B7%E6%B2%B9')
    expect(url).not.toContain('tag%5B%5D')
    expect(url).not.toContain('tag[]')
  })

  it('标量参数不受影响', () => {
    const url = http.getUri({
      url: '/metrics/paged',
      params: { page: 1, page_size: 10, status: 'all' },
    })
    expect(url).toContain('page=1')
    expect(url).toContain('page_size=10')
    expect(url).toContain('status=all')
  })

  it('空数组不产生参数', () => {
    const url = http.getUri({ url: '/metrics/paged', params: { page: 1, tag: [] } })
    expect(url).not.toContain('tag')
  })
})
