import { ElMessage } from 'element-plus'
import type { Router } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { isUnauthorized } from '@/utils/error'

export function setupRouterGuard(router: Router): void {
  router.beforeEach(async (to) => {
    const auth = useAuthStore()

    if (to.meta.public) {
      // 已登录仅拦截登录页；确认报告、404 等其他公开路由正常放行
      return to.name === 'login' && auth.isAuthenticated ? { name: 'home' } : true
    }

    if (!auth.isAuthenticated) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }

    if (!auth.user) {
      try {
        await auth.fetchMe()
      } catch (error) {
        if (isUnauthorized(error)) {
          return { name: 'login', query: { redirect: to.fullPath } }
        }
        // 网络错误：保留 token 放行，页面接口会各自报错提示
        ElMessage.error('后端连接失败，请稍后重试')
        return true
      }
    }

    return true
  })
}
