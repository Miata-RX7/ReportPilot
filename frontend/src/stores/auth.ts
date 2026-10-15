import { defineStore } from 'pinia'

import { authApi } from '@/api/auth'
import type { UserRead } from '@/api/types'
import { isUnauthorized } from '@/utils/error'
import { clearToken, getToken, setToken } from '@/utils/storage'

interface AuthState {
  token: string
  user: UserRead | null
}

/**
 * 认证状态机：
 *   未登录 --login 成功--> 已登录(token+user) --logout / 401--> 未登录(清空)
 *   已登录 --刷新页面--> token 来自 localStorage --fetchMe 成功--> user 就绪
 *                                          --fetchMe 401--> 登出
 *                                          --fetchMe 网络错误--> 保留 token（不登出）
 */
export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    token: getToken(),
    user: null,
  }),

  getters: {
    isAuthenticated: (state): boolean => !!state.token,
    isAdmin: (state): boolean => state.user?.role === 'admin',
    displayName: (state): string => state.user?.display_name || state.user?.username || '',
    /** 登录邮箱（与任务 confirmer_email 比对，用于"确认人本人可确认"） */
    email: (state): string => state.user?.email ?? '',
  },

  actions: {
    async login(username: string, password: string): Promise<void> {
      const { data } = await authApi.login({ username, password })
      this.token = data.access_token
      this.user = data.user
      setToken(data.access_token)
    },

    /**
     * 拉取当前用户。401 时清登录态并抛出；网络错误保留 token 并抛出，
     * 由调用方（路由守卫/页面）提示"后端连接失败"。
     */
    async fetchMe(): Promise<UserRead> {
      try {
        const { data } = await authApi.me()
        this.user = data
        return data
      } catch (error) {
        if (isUnauthorized(error)) {
          this.logout()
        }
        throw error
      }
    },

    logout(): void {
      this.token = ''
      this.user = null
      clearToken()
    },
  },
})
