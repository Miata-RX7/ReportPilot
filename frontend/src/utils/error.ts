import axios from 'axios'

/**
 * 统一错误解析。
 * 后端 detail 有四种形态：
 *  1. string                      -> 直接用
 *  2. { message, violations[] }   -> 模板语法错误（上传模板 422）
 *  3. ValidationError[]           -> Pydantic 校验失败（loc + msg）
 *  4. 无响应                       -> 网络错误 / 超时
 */
export interface ParsedError {
  message: string
  violations?: string[]
  status?: number
}

interface ValidationItem {
  loc?: (string | number)[]
  msg?: string
}

export function parseApiError(error: unknown): ParsedError {
  if (!axios.isAxiosError(error)) {
    return { message: error instanceof Error ? error.message : '未知错误' }
  }
  if (!error.response) {
    return {
      message:
        error.code === 'ECONNABORTED'
          ? '请求超时，请稍后重试'
          : '网络异常，请检查后端服务是否可用',
    }
  }

  const status = error.response.status
  const detail: unknown = (error.response.data as { detail?: unknown } | undefined)?.detail

  if (typeof detail === 'string') {
    return { message: detail, status }
  }

  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const obj = detail as { message?: string; violations?: string[] }
    return {
      message: obj.message ?? '请求失败',
      violations: Array.isArray(obj.violations) ? obj.violations : undefined,
      status,
    }
  }

  if (Array.isArray(detail)) {
    const parts = (detail as ValidationItem[]).map((item) => {
      const field = Array.isArray(item.loc) ? item.loc.filter((x) => x !== 'body').join('.') : ''
      return field ? `${field}: ${item.msg ?? ''}` : (item.msg ?? '')
    })
    return { message: parts.filter(Boolean).join('；') || '请求参数校验失败', status }
  }

  if (status === 401) return { message: '登录已过期，请重新登录', status }
  if (status === 403) return { message: '无权操作（需要管理员权限）', status }
  return { message: `请求失败（HTTP ${status}）`, status }
}

/** 是否为 401（用于区分"登录过期"与"后端不可用"） */
export function isUnauthorized(error: unknown): boolean {
  return axios.isAxiosError(error) && error.response?.status === 401
}

/** 是否为网络层错误（无响应） */
export function isNetworkError(error: unknown): boolean {
  return axios.isAxiosError(error) && !error.response
}
