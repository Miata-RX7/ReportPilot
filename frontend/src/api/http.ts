import axios, { type AxiosError, type AxiosInstance, type AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

import { clearToken, getToken } from '@/utils/storage'

// 扩展 axios 配置：silent 表示不弹全局错误提示（由业务层处理）
declare module 'axios' {
  export interface AxiosRequestConfig {
    silent?: boolean
  }
}

/** 默认超时 */
export const DEFAULT_TIMEOUT_MS = 30_000
/**
 * 长任务超时：报告生成为同步接口（多次 Zabbix 调用 + 1 次 AI 调用），
 * 可能耗时 30 秒~2 分钟，调用时显式传 { timeout: LONG_TIMEOUT_MS }。
 */
export const LONG_TIMEOUT_MS = 300_000

export type HttpRequestConfig = AxiosRequestConfig

let unauthorizedHandler: (() => void) | null = null

/** 由 main.ts 注册（清登录态 + 跳登录页），避免 http <-> router/store 循环依赖 */
export function setUnauthorizedHandler(handler: () => void): void {
  unauthorizedHandler = handler
}

export const http: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '/api',
  timeout: DEFAULT_TIMEOUT_MS,
})

http.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status
    const url = error.config?.url ?? ''
    const silent = (error.config as HttpRequestConfig | undefined)?.silent === true
    // 登录接口自身 401（密码错误）交由登录页展示，不触发跳转
    const isLoginRequest = url.includes('/auth/login')

    if (status === 401 && !isLoginRequest) {
      clearToken()
      if (!silent) ElMessage.error('登录已过期，请重新登录')
      unauthorizedHandler?.()
    } else if (status === 403) {
      if (!silent) ElMessage.error('无权操作（需要管理员权限）')
    } else if (status === 422) {
      // 422 语义友好（含 violations 数组），不在拦截器弹窗，交给业务层展示
    } else if (status && status >= 500) {
      if (!silent) ElMessage.error('服务端错误，请查看后端日志')
    } else if (error.code === 'ECONNABORTED') {
      if (!silent) ElMessage.error('请求超时，请稍后重试')
    } else if (!error.response) {
      if (!silent) ElMessage.error('网络异常，请检查后端服务是否可用')
    }

    return Promise.reject(error)
  },
)
