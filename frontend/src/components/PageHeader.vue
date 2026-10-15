<script setup lang="ts">
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <div class="page-header">
    <div>
      <div class="page-header__title">{{ title }}</div>
      <div v-if="description" class="page-header__desc text-muted">{{ description }}</div>
    </div>
    <div class="page-header__actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped lang="scss">
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-4);
}

.page-header__title {
  font-size: var(--font-lg);
  font-weight: 600;
  color: var(--color-text-primary);
}

.page-header__desc {
  margin-top: var(--space-1);
  font-size: var(--font-sm);
}

.page-header__actions {
  display: flex;
  gap: var(--space-2);
}
</style>
