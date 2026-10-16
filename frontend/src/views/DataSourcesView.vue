<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import axios from 'axios'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'

import { confirmDelete } from '@/components/ConfirmDialog'
import DataTable from '@/components/DataTable.vue'
import FormDialog from '@/components/FormDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import TestResultAlert from '@/components/TestResultAlert.vue'
import { dataSourceApi } from '@/api/datasources'
import type {
  DataSourceConfig,
  DataSourceCreate,
  DataSourceRead,
  DataSourceUpdate,
} from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { parseApiError } from '@/utils/error'
import { formatDateTime } from '@/utils/format'

const auth = useAuthStore()

const rows = ref<DataSourceRead[]>([])
const loading = ref(false)

const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref<number | null>(null)
const nameConflict = ref(false)
const formRef = ref<FormInstance>()

const testingId = ref<number | null>(null)
const testResult = ref<{ name: string; success: boolean; message: string } | null>(null)

const form = reactive({
  name: '',
  type: 'zabbix',
  url: '',
  timeout: 30,
  verify_ssl: true,
  secret: '',
})

const dialogTitle = computed(() => (editingId.value === null ? '新增数据源' : '编辑数据源'))

const rules = computed<FormRules>(() => ({
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  type: [{ required: true, message: '请选择类型', trigger: 'change' }],
  url: [{ required: true, message: '请输入 URL', trigger: 'blur' }],
  timeout: [
    { required: true, message: '请输入超时时间', trigger: 'blur' },
    {
      validator: (_rule, value, callback) => {
        const n = Number(value)
        if (!Number.isInteger(n) || n < 1 || n > 300) {
          callback(new Error('超时时间需为 1-300 的整数'))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
  secret:
    editingId.value === null
      ? [{ required: true, message: '请输入 API token', trigger: 'blur' }]
      : [],
}))

async function load() {
  loading.value = true
  try {
    const { data } = await dataSourceApi.list()
    rows.value = data
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  nameConflict.value = false
  Object.assign(form, { name: '', type: 'zabbix', url: '', timeout: 30, verify_ssl: true, secret: '' })
  dialogVisible.value = true
  formRef.value?.clearValidate()
}

function openEdit(row: DataSourceRead) {
  editingId.value = row.id
  nameConflict.value = false
  Object.assign(form, {
    name: row.name,
    type: row.type,
    url: row.config.url,
    timeout: row.config.timeout,
    verify_ssl: row.config.verify_ssl,
    secret: '',
  })
  dialogVisible.value = true
  formRef.value?.clearValidate()
}

async function onSubmit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  nameConflict.value = false
  try {
    // config 按类型构造：auth_type 是 Zabbix 的 JSON-RPC 鉴权方式，
    // AIOps 走的是 HTTP header `token`（实测不带 token 返回 code=9 auth failed），
    // 所以不能一律塞 auth_type: 'bearer'。
    const config: DataSourceConfig = {
      url: form.url,
      timeout: form.timeout,
      verify_ssl: form.verify_ssl,
    }
    if (form.type === 'zabbix') config.auth_type = 'bearer'
    if (editingId.value === null) {
      const payload: DataSourceCreate = {
        name: form.name,
        type: form.type,
        config,
        secret: form.secret,
      }
      await dataSourceApi.create(payload)
      ElMessage.success('数据源已创建')
    } else {
      const payload: DataSourceUpdate = {
        name: form.name,
        config,
      }
      // secret 留空 = 保留原密钥（与后端 PUT 语义一致）
      if (form.secret) payload.secret = form.secret
      await dataSourceApi.update(editingId.value, payload)
      ElMessage.success('数据源已更新')
    }
    dialogVisible.value = false
    await load()
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 409) {
      nameConflict.value = true
      await formRef.value?.validateField('name').catch(() => undefined)
    } else {
      ElMessage.error(parseApiError(error).message)
    }
  } finally {
    submitting.value = false
  }
}

async function onTest(row: DataSourceRead) {
  testingId.value = row.id
  try {
    const { data } = await dataSourceApi.test(row.id)
    testResult.value = { name: row.name, success: data.success, message: data.message }
  } catch (error) {
    // 后端 5xx / 网络异常也归到这里展示，不弹全局 toast
    testResult.value = {
      name: row.name,
      success: false,
      message: parseApiError(error).message,
    }
  } finally {
    testingId.value = null
  }
}

async function onDelete(row: DataSourceRead) {
  const ok = await confirmDelete(row.name)
  if (!ok) return
  try {
    await dataSourceApi.remove(row.id)
    ElMessage.success('数据源已删除')
    if (testResult.value?.name === row.name) testResult.value = null
    await load()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}

onMounted(load)
</script>

<template>
  <div class="page-container">
    <PageHeader title="数据源管理" description="配置 Zabbix / AIOps 连接，密钥加密存储且只回显末 4 位">
      <template #actions>
        <el-button v-if="auth.isAdmin" type="primary" @click="openCreate">新增数据源</el-button>
      </template>
    </PageHeader>

    <DataTable
      :data="rows"
      :loading="loading"
      empty-text="暂无数据源，点击右上角新增"
    >
      <template #columns>
        <el-table-column prop="name" label="名称" min-width="160" />
        <el-table-column prop="type" label="类型" width="110">
          <template #default="{ row }">
            <span class="soft-tag soft-tag--info">{{ row.type }}</span>
          </template>
        </el-table-column>
        <el-table-column label="URL" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">{{ row.config.url }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <span :class="['soft-tag', row.is_active ? 'soft-tag--success' : 'soft-tag--neutral']">
              {{ row.is_active ? '启用' : '停用' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="密钥" width="120">
          <template #default="{ row }">
            <span class="code-chip">{{ row.secret_hint || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column v-if="auth.isAdmin" label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button
              link
              type="primary"
              :loading="testingId === row.id"
              :disabled="testingId !== null && testingId !== row.id"
              @click="onTest(row)"
            >
              测试连通
            </el-button>
            <el-button link type="danger" @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </template>
    </DataTable>

    <div v-if="testResult" class="test-result">
      <TestResultAlert
        :success="testResult.success"
        :message="testResult.message"
        :title="testResult.name"
      />
    </div>

    <FormDialog
      v-model:visible="dialogVisible"
      :title="dialogTitle"
      :submitting="submitting"
      @submit="onSubmit"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <el-form-item label="名称" prop="name" :error="nameConflict ? '名称已存在' : ''">
          <el-input
            v-model="form.name"
            placeholder="如 生产 Zabbix"
            @input="nameConflict = false"
          />
        </el-form-item>

        <el-form-item label="类型" prop="type">
          <el-select v-model="form.type" class="full-width">
            <el-option label="Zabbix" value="zabbix" />
            <el-option label="AIOps" value="aiops" />
          </el-select>
          <div class="form-hint">
            {{
              form.type === 'aiops'
                ? '取数层级为「模型 → 实例 → 监控项 → 列 → 主键行」；不支持饼图'
                : 'JSON-RPC over HTTP，凭据放在 Authorization 头'
            }}
          </div>
        </el-form-item>

        <el-form-item label="URL" prop="url">
          <el-input
            v-model="form.url"
            :placeholder="
              form.type === 'aiops'
                ? 'https://aiops.example.com（填到站点根即可，/api 会自动补）'
                : 'https://zabbix.example.com'
            "
          />
        </el-form-item>

        <el-form-item label="超时(秒)" prop="timeout">
          <el-input-number v-model="form.timeout" :min="1" :max="300" />
        </el-form-item>

        <el-form-item label="校验 SSL" prop="verify_ssl">
          <el-switch v-model="form.verify_ssl" />
        </el-form-item>

        <el-form-item :label="form.type === 'aiops' ? 'API Token' : 'API Token'" prop="secret">
          <el-input
            v-model="form.secret"
            type="password"
            show-password
            :placeholder="
              editingId !== null
                ? '留空则不修改'
                : form.type === 'aiops'
                  ? 'AIOps token（utk- 开头）'
                  : 'Zabbix API token'
            "
          />
          <div class="form-hint">
            密钥加密存储，保存后只回显末 4 位；AIOps token 以 <code>token</code> 请求头发送
          </div>
        </el-form-item>
      </el-form>
    </FormDialog>
  </div>
</template>

<style scoped lang="scss">
.test-result {
  margin-top: var(--space-4);
}

.full-width {
  width: 100%;
}
</style>
