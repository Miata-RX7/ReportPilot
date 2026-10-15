<script setup lang="ts">
/** 首页（占位）：内容后续设计，先放入口卡片。 */
import { useRouter } from 'vue-router'

const router = useRouter()

const entries = [
  { title: '数据源管理', desc: '维护 Zabbix 等数据源与连接凭据', path: '/datasources' },
  { title: '指标管理', desc: '定义取数范围与聚合口径，产出计算变量', path: '/metrics' },
  { title: '模板管理', desc: '上传 Word 模板、配置变量绑定与在线编辑', path: '/templates' },
  { title: '报告任务', desc: '按 cron 周期生成报告，到点发确认邮件给确认人', path: '/report-tasks' },
  { title: '系统设置', desc: '推送记录与邮件/调度运行时配置', path: '/settings' },
]
</script>

<template>
  <div class="page-container home">
    <el-card shadow="never" class="intro">
      <template #header>
        <span class="intro-title">ReportPilot</span>
      </template>
      <p class="text-muted">
        运维月报自动生成系统：数据源 → 指标 → 模板 → 报告任务 → 确认人确认 → 邮件推送。
      </p>
      <el-alert
        type="info"
        show-icon
        :closable="false"
        title="首页内容待设计，当前仅提供功能入口"
      />
    </el-card>

    <div class="cards">
      <el-card
        v-for="e in entries"
        :key="e.path"
        shadow="hover"
        class="entry-card"
        data-testid="home-entry"
        @click="router.push(e.path)"
      >
        <div class="entry-title">{{ e.title }}</div>
        <div class="text-muted entry-desc">{{ e.desc }}</div>
      </el-card>
    </div>
  </div>
</template>

<style scoped lang="scss">
/* 外层由 .page-container 统一内边距与最大宽度 */
.home {
  display: block;
}

.intro-title {
  font-size: var(--font-lg);
  font-weight: 600;
}

.intro :deep(.el-alert) {
  margin-top: var(--space-3);
}

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--space-3);
  margin-top: var(--space-4);
}

.entry-card {
  cursor: pointer;
}

.entry-title {
  font-size: var(--font-md);
  font-weight: 500;
  margin-bottom: var(--space-1);
}

.entry-desc {
  font-size: var(--font-sm);
}
</style>
