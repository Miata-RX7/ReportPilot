<script setup lang="ts">
/**
 * 新增/编辑复用弹窗：父组件用 editingId === null 判断模式。
 * slots: default（表单体）、footer（可选，默认「取消 / 确定」）
 */
const visible = defineModel<boolean>('visible', { required: true })

withDefaults(
  defineProps<{
    title: string
    submitting?: boolean
    /** 默认取 token 中的 --layout-dialog-width */
    width?: string
  }>(),
  {
    submitting: false,
    width: 'var(--layout-dialog-width)',
  },
)

const emit = defineEmits<{ (e: 'submit'): void }>()
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="title"
    :width="width"
    :close-on-click-modal="false"
    :close-on-press-escape="!submitting"
    :show-close="!submitting"
    destroy-on-close
  >
    <slot />
    <template #footer>
      <slot name="footer">
        <el-button :disabled="submitting" @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="emit('submit')">
          确定
        </el-button>
      </slot>
    </template>
  </el-dialog>
</template>
