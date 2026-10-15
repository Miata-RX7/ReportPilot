<script setup lang="ts">
/** 连通性测试结果展示：成功绿 / 失败红，文案由后端给出（已分类三类错误） */
withDefaults(
  defineProps<{
    success: boolean
    message: string
    /** 可选上下文标题，如数据源名称 */
    title?: string
  }>(),
  { title: '' },
)
</script>

<template>
  <el-alert
    :type="success ? 'success' : 'error'"
    :title="title ? `${title}：${message}` : message"
    show-icon
    :closable="false"
  />
</template>
