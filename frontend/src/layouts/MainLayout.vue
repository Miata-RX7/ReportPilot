<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'

import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

// 顶部横向导航（首页置顶）
const navItems = [
  { path: '/', name: 'home', title: '首页', icon: 'HomeFilled' },
  { path: '/datasources', name: 'datasources', title: '数据源管理', icon: 'Link' },
  { path: '/metrics', name: 'metrics', title: '指标管理', icon: 'DataLine' },
  { path: '/templates', name: 'templates', title: '模板管理', icon: 'Document' },
  { path: '/report-tasks', name: 'reportTasks', title: '报告任务', icon: 'AlarmClock' },
  { path: '/settings', name: 'settings', title: '系统设置', icon: 'Setting' },
]

/** 首页（"/"）在 el-menu 里需要唯一 index，路由本身也是 "/" */
function navIndex(path: string): string {
  return path === '/' ? 'home' : path
}

const activeIndex = computed(() => navIndex(route.path))

async function onLogout() {
  await ElMessageBox.confirm('确认退出登录？', '提示', { type: 'warning' })
  auth.logout()
  void router.replace({ name: 'login' })
}
</script>

<template>
  <el-container class="layout">
    <el-header class="topbar">
      <div class="brand">
        <span class="logo">ReportPilot</span>
      </div>

      <el-menu
        :default-active="activeIndex"
        mode="horizontal"
        router
        class="menu"
        data-testid="top-nav"
      >
        <el-menu-item
          v-for="item in navItems"
          :key="item.path"
          :index="navIndex(item.path)"
          :route="{ name: item.name }"
          :data-testid="`nav-${item.path}`"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
        </el-menu-item>
      </el-menu>

      <div class="header-user">
        <span class="user-name">{{ auth.displayName }}</span>
        <span class="role-chip">{{ auth.isAdmin ? '管理员' : '普通用户' }}</span>
        <button type="button" class="logout-btn" @click="onLogout">退出登录</button>
      </div>
    </el-header>

    <el-main class="main">
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped lang="scss">
.layout {
  height: 100vh;
  flex-direction: column;
}

/* 顶部横向导航（深色） */
.topbar {
  height: var(--header-height);
  padding: 0 var(--space-5);
  display: flex;
  align-items: center;
  gap: var(--space-5);
  background-color: var(--header-bg);
  border-bottom: 1px solid var(--sidebar-item-hover-bg);
  flex: none;
}

.brand {
  display: flex;
  align-items: center;
  flex: none;
}

.logo {
  font-size: var(--font-lg);
  font-weight: 600;
  color: var(--sidebar-logo-color);
  white-space: nowrap;
}

.menu {
  flex: 1 1 auto;
  min-width: 0;
  background-color: transparent;
  border-bottom: none;
  --el-menu-bg-color: transparent;
  --el-menu-text-color: var(--sidebar-text);
  --el-menu-active-color: var(--sidebar-text-active);
  --el-menu-hover-bg-color: var(--sidebar-item-hover-bg);
  --el-menu-item-height: var(--sidebar-item-height);
  --el-menu-item-font-size: var(--font-base);
}

.menu :deep(.el-menu-item) {
  color: var(--sidebar-text);
}

/* 横向菜单的下划线改用背景块，选中态更明显 */
.menu :deep(.el-menu-item.is-active) {
  background-color: var(--sidebar-item-active-bg);
  color: var(--sidebar-text-active);
  border-bottom: 2px solid var(--sidebar-text-active);
}

.menu :deep(.el-menu-item:hover) {
  background-color: var(--sidebar-item-hover-bg);
  color: var(--sidebar-text-active);
}

.header-user {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex: none;
}

.user-name {
  font-size: var(--font-sm);
  color: var(--sidebar-text);
}

/* 软标签：浅底 + 深字（此处为深色顶栏上的中性变体） */
.role-chip {
  height: var(--tag-height);
  padding: 0 var(--tag-padding-x);
  line-height: var(--tag-height);
  font-size: var(--tag-font-size);
  border-radius: var(--tag-radius);
  color: var(--sidebar-text);
  background-color: var(--sidebar-item-hover-bg);
}

.logout-btn {
  padding: 0;
  border: none;
  background: none;
  cursor: pointer;
  font-family: var(--font-sans);
  font-size: var(--font-sm);
  color: var(--header-text);
  transition: color 0.2s;
}

.logout-btn:hover {
  color: var(--sidebar-text-active);
}

.main {
  padding: 0;
  background-color: var(--color-bg);
  overflow-y: auto;
}
</style>
