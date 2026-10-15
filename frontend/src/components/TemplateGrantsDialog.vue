<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { templateApi } from '@/api/templates'
import { userApi } from '@/api/users'
import type { GrantItem, TemplatePermission, UserRead } from '@/api/types'
import { parseApiError } from '@/utils/error'

const props = defineProps<{
  modelValue: boolean
  templateId: number | null
  templateName: string
  permission: TemplatePermission
  ownerId: number | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'changed'): void
}>()

const visible = ref(props.modelValue)
const loading = ref(false)
const saving = ref(false)
const users = ref<UserRead[]>([])
const grants = ref<GrantItem[]>([])
const newUserId = ref<number | null>(null)
const newPermission = ref<'view' | 'edit'>('view')
const transferUserId = ref<number | null>(null)

const userName = computed(() => {
  const map = new Map(users.value.map((u) => [u.id, u.display_name || u.username]))
  return (id: number) => map.get(id) ?? `#${id}`
})

const isManage = computed(() => props.permission === 'manage')

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
    const [g, u] = await Promise.all([
      templateApi.grants(props.templateId),
      userApi.list(),
    ])
    grants.value = g.data.map((x) => ({ user_id: x.user_id, permission: x.permission }))
    users.value = u.data
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    loading.value = false
  }
}

function addGrant() {
  if (newUserId.value === null) {
    ElMessage.warning('请选择用户')
    return
  }
  if (grants.value.some((g) => g.user_id === newUserId.value)) {
    ElMessage.warning('该用户已有授权')
    return
  }
  grants.value.push({ user_id: newUserId.value, permission: newPermission.value })
  newUserId.value = null
  newPermission.value = 'view'
}

function removeGrant(idx: number) {
  grants.value.splice(idx, 1)
}

async function save() {
  if (props.templateId === null) return
  saving.value = true
  try {
    await templateApi.putGrants(props.templateId, grants.value)
    ElMessage.success('授权已保存')
    emit('changed')
    await load()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    saving.value = false
  }
}

async function transfer() {
  if (props.templateId === null || transferUserId.value === null) {
    ElMessage.warning('请选择新属主')
    return
  }
  saving.value = true
  try {
    await templateApi.transfer(props.templateId, transferUserId.value)
    ElMessage.success('属主已转移')
    transferUserId.value = null
    emit('changed')
    await load()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="`授权管理 - ${templateName}`"
    width="640px"
    destroy-on-close
    data-testid="template-grants-dialog"
  >
    <div v-loading="loading">
      <div class="owner-row">
        <span class="label">属主</span>
        <span class="owner-name" data-testid="grants-owner">
          {{ ownerId !== null ? userName(ownerId) : '—' }}
        </span>
      </div>

      <el-table :data="grants" data-testid="grants-table">
        <el-table-column label="用户" min-width="160">
          <template #default="{ row }">{{ userName(row.user_id) }}</template>
        </el-table-column>
        <el-table-column label="权限" width="140">
          <template #default="{ row }">
            <el-select v-model="row.permission" :disabled="!isManage">
              <el-option label="只读" value="view" />
              <el-option label="编辑" value="edit" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column v-if="isManage" label="操作" width="90">
          <template #default="{ $index }">
            <el-button link type="danger" @click="removeGrant($index)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="isManage" class="add-row">
        <el-select v-model="newUserId" placeholder="选择用户" filterable class="user-select">
          <el-option
            v-for="u in users"
            :key="u.id"
            :label="u.display_name || u.username"
            :value="u.id"
          />
        </el-select>
        <el-select v-model="newPermission" class="perm-select">
          <el-option label="只读" value="view" />
          <el-option label="编辑" value="edit" />
        </el-select>
        <el-button @click="addGrant">添加授权</el-button>
      </div>

      <el-divider v-if="isManage" />

      <div v-if="isManage" class="add-row">
        <span class="label">转移属主</span>
        <el-select v-model="transferUserId" placeholder="选择新属主" filterable class="user-select">
          <el-option
            v-for="u in users"
            :key="u.id"
            :label="u.display_name || u.username"
            :value="u.id"
          />
        </el-select>
        <el-button type="warning" :loading="saving" @click="transfer">确认转移</el-button>
      </div>
    </div>

    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
      <el-button v-if="isManage" type="primary" :loading="saving" @click="save">
        保存授权
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped lang="scss">
.owner-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.label {
  color: var(--color-text-tertiary);
  font-size: var(--font-sm);
}

.owner-name {
  font-weight: 600;
}

.add-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.user-select {
  width: 220px;
}

.perm-select {
  width: 110px;
}
</style>
