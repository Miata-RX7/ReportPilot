<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import OnlyofficeEmbed from '@/components/OnlyofficeEmbed.vue'

const props = defineProps<{
  modelValue: boolean
  templateId: number | null
  templateName: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'saved'): void
}>()

const visible = ref(props.modelValue)
const disabledReason = ref('')

watch(
  () => props.modelValue,
  (v) => {
    visible.value = v
    if (v) disabledReason.value = ''
  },
  { immediate: true },
)
watch(visible, (v) => emit('update:modelValue', v))

function onSaved() {
  ElMessage.success('文档已保存为新版本')
  emit('saved')
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="`在线编辑 - ${templateName}`"
    width="94%"
    top="3vh"
    destroy-on-close
    :close-on-click-modal="false"
    data-testid="template-editor-dialog"
  >
    <el-alert
      v-if="disabledReason"
      type="warning"
      show-icon
      :closable="false"
      :title="disabledReason"
      data-testid="editor-disabled-alert"
    />
    <div v-else class="editor-host">
      <OnlyofficeEmbed
        v-if="templateId !== null"
        :template-id="templateId"
        mode="edit"
        reload-on-close
        @disabled="(r) => (disabledReason = r)"
        @error="(m) => ElMessage.error(m)"
        @saved="onSaved"
      />
    </div>
    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped lang="scss">
.editor-host {
  height: 74vh;
}
</style>
