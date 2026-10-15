import { http } from './http'
import type { LoginRequest, TokenResponse, UserRead } from './types'

export const authApi = {
  login(payload: LoginRequest) {
    // silent：登录失败的提示由登录页展示（401 不触发全局跳转）
    return http.post<TokenResponse>('/auth/login', payload, { silent: true })
  },
  me() {
    return http.get<UserRead>('/auth/me')
  },
}
