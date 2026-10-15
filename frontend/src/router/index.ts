import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    children: [
      {
        path: '',
        name: 'home',
        component: () => import('@/views/HomeView.vue'),
        meta: { title: '首页' },
      },
      {
        path: 'datasources',
        name: 'datasources',
        component: () => import('@/views/DataSourcesView.vue'),
        meta: { title: '数据源管理' },
      },
      {
        path: 'metrics',
        name: 'metrics',
        component: () => import('@/views/MetricsView.vue'),
        meta: { title: '指标管理' },
      },
      {
        path: 'templates',
        name: 'templates',
        component: () => import('@/views/TemplatesView.vue'),
        meta: { title: '模板管理' },
      },
      {
        path: 'report-tasks',
        name: 'reportTasks',
        component: () => import('@/views/ReportTasksView.vue'),
        meta: { title: '报告任务' },
      },
      {
        // 报告管理页已下线：报告实例改在「报告任务 → 执行记录」弹窗内查看
        path: 'reports',
        redirect: { name: 'reportTasks' },
      },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('@/views/SystemSettingsView.vue'),
        meta: { title: '系统设置' },
      },
      {
        // 推送记录已迁入系统设置；旧书签重定向过去而不是 404
        path: 'push-records',
        redirect: { name: 'settings' },
      },
    ],
  },
  {
    // 报告确认页：**公开路由**（凭邮件里的一次性 token，确认人无需登录）
    path: '/confirm/:id',
    name: 'confirmReport',
    component: () => import('@/views/ConfirmReportView.vue'),
    meta: { public: true },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'notFound',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { public: true },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
