<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { templateApi } from '@/api/templates'
import type { TemplatePermission, VersionRead } from '@/api/types'
import { canEdit } from '@/utils/template'
import { parseApiError } from '@/utils/error'
import { formatDateTime } from '@/utils/format'

const props = defineProps<{
  modelValue: boolean
  templateId: number | null
  templateName: string
  permission: TemplatePermission
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'activated'): void
}>()

const visible = ref(props.modelValue)
const loading = ref(false)
const versions = ref<VersionRead[]>([])

watch(
  () => props.modelValue,
  (v) => {
    visible.value = v
    if (v) void load()
  },
  { immediate: true },
)
watch(visible, (v) => emit('update:modelValue', v))

async function load() {
  if (props.templateId === null) return
  loading.value = true
  try {
    versions.value = (await templateApi.versions(props.templateId)).data
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    loading.value = false
  }
}

async function activate(v: VersionRead) {
  if (props.templateId === null) return
  try {
    await templateApi.activateVersion(props.templateId, v.id)
    ElMessage.success(`已切换为版本 v${v.version_no}`)
    await load()
    emit('activated')
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="`版本历史 - ${templateName}`"
    width="720px"
    destroy-on-close
    data-testid="template-versions-dialog"
  >
    <el-table v-loading="loading" :data="versions" data-testid="versions-table">
      <el-table-column label="版本" width="90">
        <template #default="{ row }">v{{ row.version_no }}</template>
      </el-table-column>
      <el-table-column prop="file_name" label="文件名" min-width="180" show-overflow-tooltip />
      <el-table-column label="变量数" width="90">
        <template #default="{ row }">{{ row.placeholder_count }}</template>
      </el-table-column>
      <el-table-column label="来源" width="90">
        <template #default="{ row }">{{ row.source === 'edit' ? '在线编辑' : '上传' }}</template>
      </el-table-column>
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <span :class="['soft-tag', row.is_current ? 'soft-tag--success' : 'soft-tag--neutral']">
            {{ row.is_current ? '当前' : '历史' }}
          </span>
        </template>
      </el-table-column>
      <el-table-column v-if="canEdit(permission)" label="操作" width="110" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="!row.is_current"
            link
            type="primary"
            @click="activate(row)"
          >
            设为当前
          </el-button>
        </template>
      </el-table-column>
    </el-table>
    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>
