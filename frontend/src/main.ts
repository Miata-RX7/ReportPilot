import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import ElementPlus from 'element-plus'
import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import { setupRouterGuard } from './router/guard'
import { setUnauthorizedHandler } from './api/http'
import { useAuthStore } from './stores/auth'
import './styles/tokens.scss'
// Element Plus 主题：用 SCSS 入口（由 vite additionalData 注入变量覆盖），
// 不能用 dist/index.css，否则 SCSS 变量覆盖不生效
import 'element-plus/theme-chalk/src/index.scss'
import './styles/main.css'

const app = createApp(App)

app.use(createPinia())
setupRouterGuard(router)
app.use(router)
app.use(ElementPlus)

for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

// 401 统一处理：清登录态并跳登录页（带上回跳地址），登录页自身 401 不触发
setUnauthorizedHandler(() => {
  useAuthStore().logout()
  const current = router.currentRoute.value
  if (current.name !== 'login') {
    void router.replace({ name: 'login', query: { redirect: current.fullPath } })
  }
})

app.mount('#app')
