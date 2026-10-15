<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import OnlyofficeEmbed from '@/components/OnlyofficeEmbed.vue'
import PeriodPicker from '@/components/PeriodPicker.vue'
import { templateApi } from '@/api/templates'
import type { TemplatePermission } from '@/api/types'
import { canEdit } from '@/utils/template'
import { downloadBlob, filenameFromDisposition } from '@/utils/download'
import { parseApiError } from '@/utils/error'
import { previousPeriod } from '@/utils/format'

const props = defineProps<{
  modelValue: boolean
  templateId: number | null
  templateName: string
  permission: TemplatePermission
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'edit'): void
}>()

const visible = ref(props.modelValue)
const period = ref(previousPeriod())
const downloading = ref(false)
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

async function downloadPreview() {
  if (props.templateId === null || !period.value) {
    ElMessage.warning('请选择统计周期')
    return
  }
  downloading.value = true
  try {
    const resp = await templateApi.preview(props.templateId, period.value)
    const disposition = (resp.headers as Record<string, string>)['content-disposition']
    const filename = filenameFromDisposition(
      disposition,
      `preview_${props.templateName}_${period.value}.docx`,
    )
    downloadBlob(resp.data as Blob, filename)
    ElMessage.success('预览文件已下载')
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    downloading.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="`模板预览（原始文档） - ${templateName}`"
    width="90%"
    top="4vh"
    destroy-on-close
    class="preview-dialog"
    data-testid="template-preview-dialog"
  >
    <div class="preview-toolbar">
      <el-button
        v-if="canEdit(permission)"
        type="primary"
        data-testid="preview-edit-btn"
        @click="emit('edit')"
      >
        在线编辑
      </el-button>
      <div class="toolbar-right">
        <PeriodPicker
          v-model="period"
          testid="template-preview-period"
        />
        <el-button :loading="downloading" @click="downloadPreview">下载渲染预览</el-button>
      </div>
    </div>

    <el-alert
      v-if="disabledReason"
      type="warning"
      show-icon
      :closable="false"
      :title="disabledReason"
    />
    <div v-else class="docx-host">
      <OnlyofficeEmbed
        v-if="templateId !== null"
        :template-id="templateId"
        mode="view"
        @disabled="(r) => (disabledReason = r)"
        @error="(m) => ElMessage.error(m)"
      />
    </div>
  </el-dialog>
</template>

<style scoped lang="scss">
.preview-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
}

.docx-host {
  height: 72vh;
  border: 1px solid var(--color-border);
  border-radius: var(--card-radius);
  overflow: hidden;
}
</style>
