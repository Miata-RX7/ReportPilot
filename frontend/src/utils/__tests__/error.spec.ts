import { AxiosError, AxiosHeaders } from 'axios'
import { describe, expect, it } from 'vitest'

import { isNetworkError, isUnauthorized, parseApiError } from '@/utils/error'

function makeAxiosError(status?: number, data?: unknown, code?: string): AxiosError {
  const error = new AxiosError('request failed', code)
  if (status !== undefined) {
    error.response = {
      status,
      statusText: '',
      data,
      headers: {},
      config: { headers: new AxiosHeaders() },
    } as AxiosError['response']
  }
  return error
}

describe('parseApiError', () => {
  it('解析字符串 detail', () => {
    const result = parseApiError(makeAxiosError(409, { detail: '数据源名称已存在: prod' }))
    expect(result).toEqual({ message: '数据源名称已存在: prod', status: 409 })
  })

  it('解析模板语法错误对象（message + violations）', () => {
    const result = parseApiError(
      makeAxiosError(422, {
        detail: { message: '模板包含不支持的语法', violations: ["[word/document.xml] 非法片段: '{% if x %}'"] },
      }),
    )
    expect(result.message).toBe('模板包含不支持的语法')
    expect(result.violations).toHaveLength(1)
  })

  it('解析 Pydantic 校验错误数组', () => {
    const result = parseApiError(
      makeAxiosError(422, {
        detail: [
          { loc: ['body', 'period'], msg: 'String should match pattern' },
          { loc: ['body', 'recipient_emails'], msg: 'value is not a valid email address' },
        ],
      }),
    )
    expect(result.message).toBe(
      'period: String should match pattern；recipient_emails: value is not a valid email address',
    )
  })

  it('无响应且超时 -> 超时文案', () => {
    expect(parseApiError(makeAxiosError(undefined, undefined, 'ECONNABORTED')).message).toBe(
      '请求超时，请稍后重试',
    )
  })

  it('无响应且非超时 -> 网络异常文案', () => {
    expect(parseApiError(makeAxiosError()).message).toBe('网络异常，请检查后端服务是否可用')
  })

  it('无 detail 时按状态码兜底', () => {
    expect(parseApiError(makeAxiosError(403)).message).toBe('无权操作（需要管理员权限）')
    expect(parseApiError(makeAxiosError(500)).message).toBe('请求失败（HTTP 500）')
  })

  it('非 axios 错误 -> 取 Error.message', () => {
    expect(parseApiError(new Error('boom')).message).toBe('boom')
  })
})

describe('错误类型判定', () => {
  it('isUnauthorized 仅对 401 为真', () => {
    expect(isUnauthorized(makeAxiosError(401))).toBe(true)
    expect(isUnauthorized(makeAxiosError(403))).toBe(false)
    expect(isUnauthorized(makeAxiosError())).toBe(false)
  })

  it('isNetworkError 仅对无响应为真', () => {
    expect(isNetworkError(makeAxiosError())).toBe(true)
    expect(isNetworkError(makeAxiosError(401))).toBe(false)
  })
})
