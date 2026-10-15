<script setup lang="ts">
/**
 * 系统设置：推送记录 + 运行时配置概览 + 用户管理。
 * 运行时配置中，邮件与 AI 集成可直接编辑保存（需管理员）；密钥只写不读。
 */
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { confirmDanger } from '@/components/ConfirmDialog'
import AiPromptHistoryDrawer from '@/components/AiPromptHistoryDrawer.vue'
import AiSystemPromptDialog from '@/components/AiSystemPromptDialog.vue'
import FormDialog from '@/components/FormDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import PushRecordsPanel from '@/components/PushRecordsPanel.vue'
import {
  systemApi,
  type AiSettingsUpdate,
  type MailSettingsUpdate,
  type SystemSettings,
} from '@/api/system'
import { userApi } from '@/api/users'
import type { UserRead } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { parseApiError } from '@/utils/error'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const auth = useAuthStore()
const tab = ref('push')
const settings = ref<SystemSettings | null>(null)
const loading = ref(false)

async function loadSettings() {
  loading.value = true
  try {
    settings.value = await systemApi.settings()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    loading.value = false
  }
}

/** 就绪状态标签 */
function readyTag(ok: boolean): { type: 'success' | 'warning'; text: string } {
  return ok ? { type: 'success', text: '已就绪' } : { type: 'warning', text: '未配置' }
}

function yesNo(v: boolean): string {
  return v ? '是' : '否'
}

/* ---- 集成配置编辑（邮件 / AI，需管理员） ---- */
const cfgSaving = ref(false)
const mailDialogVisible = ref(false)
const aiDialogVisible = ref(false)
/** 系统提示词（两段规则文本）编辑器 + 版本历史 */
const promptDialogVisible = ref(false)
const promptHistoryVisible = ref(false)
const mailForm = reactive({
  smtp_host: '',
  smtp_port: 25,
  smtp_username: '',
  smtp_password: '',
  smtp_from: '',
  smtp_use_tls: false,
  app_base_url: '',
})
const aiForm = reactive({
  ai_base_url: '',
  ai_model: '',
  ai_api_key: '',
})

function openMailEdit() {
  if (!settings.value) return
  mailForm.smtp_host = settings.value.smtp_host
  mailForm.smtp_port = settings.value.smtp_port
  mailForm.smtp_username = settings.value.smtp_username
  mailForm.smtp_from = settings.value.smtp_from
  mailForm.smtp_use_tls = settings.value.smtp_use_tls
  mailForm.app_base_url = settings.value.app_base_url
  // 密码绝不回显：留空即为"不修改"
  mailForm.smtp_password = ''
  mailDialogVisible.value = true
}

async function saveMail() {
  if (!mailForm.smtp_host.trim()) {
    ElMessage.warning('请填写 SMTP 服务器')
    return
  }
  if (!mailForm.smtp_from.trim()) {
    ElMessage.warning('请填写发件人')
    return
  }
  if (!mailForm.app_base_url.trim()) {
    ElMessage.warning('请填写确认链接基础地址')
    return
  }
  const payload: MailSettingsUpdate = {
    smtp_host: mailForm.smtp_host.trim(),
    smtp_port: mailForm.smtp_port,
    smtp_username: mailForm.smtp_username.trim(),
    smtp_from: mailForm.smtp_from.trim(),
    smtp_use_tls: mailForm.smtp_use_tls,
    app_base_url: mailForm.app_base_url.trim(),
  }
  // 留空 = 不修改原密码（而不是把密码置空）
  if (mailForm.smtp_password.trim()) payload.smtp_password = mailForm.smtp_password
  cfgSaving.value = true
  try {
    settings.value = await systemApi.updateMail(payload)
    ElMessage.success('邮件集成已保存')
    mailDialogVisible.value = false
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    cfgSaving.value = false
  }
}

function openAiEdit() {
  if (!settings.value) return
  aiForm.ai_base_url = settings.value.ai_base_url
  aiForm.ai_model = settings.value.ai_model
  // API Key 绝不回显：留空即为"不修改"
  aiForm.ai_api_key = ''
  aiDialogVisible.value = true
}

async function saveAi() {
  if (!aiForm.ai_model.trim()) {
    ElMessage.warning('请填写 AI 模型')
    return
  }
  const payload: AiSettingsUpdate = {
    ai_base_url: aiForm.ai_base_url.trim(),
    ai_model: aiForm.ai_model.trim(),
  }
  // 留空 = 不修改原 API Key
  if (aiForm.ai_api_key.trim()) payload.ai_api_key = aiForm.ai_api_key.trim()
  cfgSaving.value = true
  try {
    settings.value = await systemApi.updateAi(payload)
    ElMessage.success('AI 集成已保存')
    aiDialogVisible.value = false
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    cfgSaving.value = false
  }
}

/* ---- 用户管理 ---- */
const users = ref<UserRead[]>([])
const usersLoading = ref(false)
const userDialogVisible = ref(false)
const userSaving = ref(false)
const editingUserId = ref<number | null>(null)
const userForm = reactive({
  username: '',
  display_name: '',
  email: '',
  role: 'user' as 'admin' | 'user',
})
const userCreateVisible = ref(false)
const newUser = reactive({
  username: '',
  display_name: '',
  email: '',
  password: '',
  role: 'user' as 'admin' | 'user',
})

async function loadUsers() {
  usersLoading.value = true
  try {
    // 管理员看全部（含停用）；普通用户只看启用用户（后端对 include_inactive 限管理员）
    users.value = (auth.isAdmin ? await userApi.listAll() : await userApi.list()).data
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    usersLoading.value = false
  }
}

function openUserCreate() {
  newUser.username = ''
  newUser.display_name = ''
  newUser.email = ''
  newUser.password = ''
  newUser.role = 'user'
  userCreateVisible.value = true
}

async function saveNewUser() {
  const username = newUser.username.trim()
  const email = newUser.email.trim()
  if (username.length < 2) {
    ElMessage.warning('登录名至少 2 个字符')
    return
  }
  if (!EMAIL_RE.test(email)) {
    ElMessage.warning('请填写正确的邮箱')
    return
  }
  if (newUser.password.length < 8) {
    ElMessage.warning('初始密码至少 8 位')
    return
  }
  userSaving.value = true
  try {
    await userApi.create({
      username,
      display_name: newUser.display_name.trim(),
      email,
      password: newUser.password,
      role: newUser.role,
      is_active: true,
    })
    ElMessage.success(`用户 ${username} 已创建`)
    userCreateVisible.value = false
    await loadUsers()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    userSaving.value = false
  }
}

function openUserEdit(row: UserRead) {
  editingUserId.value = row.id
  userForm.username = row.username
  userForm.display_name = row.display_name
  userForm.email = row.email
  userForm.role = row.role === 'admin' ? 'admin' : 'user'
  userDialogVisible.value = true
}

async function saveUser() {
  if (editingUserId.value === null) return
  if (!userForm.email.trim() || !EMAIL_RE.test(userForm.email.trim())) {
    ElMessage.warning('请填写正确的邮箱')
    return
  }
  userSaving.value = true
  try {
    await userApi.update(editingUserId.value, {
      display_name: userForm.display_name.trim(),
      email: userForm.email.trim(),
      role: userForm.role,
    })
    ElMessage.success('已保存')
    userDialogVisible.value = false
    await loadUsers()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    userSaving.value = false
  }
}

async function onToggleUser(row: UserRead) {
  try {
    await userApi.update(row.id, { is_active: !row.is_active })
    ElMessage.success(row.is_active ? '已停用' : '已启用')
    await loadUsers()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}

async function onDeleteUser(row: UserRead) {
  const ok = await confirmDanger(
    '删除用户',
    `确认删除用户「${row.display_name || row.username}」？\n若该用户仍被模板/任务引用，请改为「停用」。`,
  )
  if (!ok) return
  try {
    await userApi.remove(row.id)
    ElMessage.success('已删除')
    await loadUsers()
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  }
}

onMounted(() => {
  void loadSettings()
  void loadUsers()
})
</script>

<template>
  <div class="page-container">
    <PageHeader
      title="系统设置"
      description="推送记录与运行时配置（密钥只写不读；邮件 / AI 集成可在卡片右上角编辑）"
    />

    <el-tabs v-model="tab" class="settings-tabs" data-testid="settings-tabs">
      <!-- 推送记录（原独立页面迁入） -->
      <el-tab-pane label="推送记录" name="push" data-testid="settings-tab-push">
        <PushRecordsPanel />
      </el-tab-pane>

      <!-- 运行时配置 -->
      <el-tab-pane label="运行时配置" name="runtime" data-testid="settings-tab-runtime">
        <div v-loading="loading" class="runtime">
          <el-alert
            type="info"
            show-icon
            :closable="false"
            class="runtime-hint"
            title="此处展示影响行为的公开配置与“是否已配置”，不会显示任何密码或密钥；邮件与 AI 集成可点卡片右上角“编辑”直接保存，其余项请编辑部署环境的 .env。"
          />

          <el-row :gutter="16">
            <el-col :span="12">
              <el-card shadow="never" class="cfg-card" data-testid="cfg-card-mail">
                <template #header>
                  <div class="card-head">
                    <span>邮件发送（SMTP）</span>
                    <div class="card-head-right">
                      <el-tag
                        v-if="settings"
                        :type="readyTag(settings.smtp_ready).type"
                        size="small"
                        data-testid="cfg-tag-smtp"
                      >
                        {{ readyTag(settings.smtp_ready).text }}
                      </el-tag>
                      <el-button
                        v-if="auth.isAdmin && settings"
                        link
                        type="primary"
                        data-testid="cfg-edit-mail"
                        @click="openMailEdit"
                      >
                        编辑
                      </el-button>
                    </div>
                  </div>
                </template>
                <el-descriptions :column="1" size="small" border v-if="settings">
                  <el-descriptions-item label="SMTP 服务器">
                    {{ settings.smtp_host || '—' }}:{{ settings.smtp_port }}
                  </el-descriptions-item>
                  <el-descriptions-item label="发件人">
                    {{ settings.smtp_from || '—' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="认证密码">
                    <el-tag
                      :type="settings.smtp_secret_set ? 'success' : 'info'"
                      size="small"
                    >
                      {{ settings.smtp_secret_set ? '已配置' : '未设置' }}
                    </el-tag>
                  </el-descriptions-item>
                  <el-descriptions-item label="启用 TLS">
                    {{ yesNo(settings.smtp_use_tls) }}
                  </el-descriptions-item>
                  <el-descriptions-item label="确认链接基础地址">
                    <span class="code-chip">{{ settings.app_base_url }}</span>
                  </el-descriptions-item>
                </el-descriptions>
                <el-skeleton v-else :rows="5" animated />
              </el-card>
            </el-col>

            <el-col :span="12">
              <el-card shadow="never" class="cfg-card" data-testid="cfg-card-ai">
                <template #header>
                  <div class="card-head">
                    <span>AI 集成</span>
                    <div class="card-head-right">
                      <el-tag
                        v-if="settings"
                        :type="readyTag(settings.ai_ready).type"
                        size="small"
                        data-testid="cfg-tag-ai"
                      >
                        {{ readyTag(settings.ai_ready).text }}
                      </el-tag>
                      <el-button
                        v-if="auth.isAdmin && settings"
                        link
                        type="primary"
                        data-testid="cfg-edit-ai"
                        @click="openAiEdit"
                      >
                        编辑
                      </el-button>
                    </div>
                  </div>
                </template>
                <el-descriptions :column="1" size="small" border v-if="settings">
                  <el-descriptions-item label="接口地址">
                    <span class="code-chip">{{ settings.ai_base_url || '—' }}</span>
                  </el-descriptions-item>
                  <el-descriptions-item label="AI 模型">
                    <span class="code-chip">{{ settings.ai_model }}</span>
                  </el-descriptions-item>
                  <el-descriptions-item label="API Key">
                    <el-tag :type="settings.ai_secret_set ? 'success' : 'info'" size="small">
                      {{ settings.ai_secret_set ? '已配置' : '未设置' }}
                    </el-tag>
                  </el-descriptions-item>
                  <el-descriptions-item label="系统提示词">
                    <div class="prompt-row">
                      <el-tag
                        :type="settings.ai_prompt_custom ? 'primary' : 'info'"
                        size="small"
                        data-testid="cfg-tag-prompt"
                      >
                        {{ settings.ai_prompt_custom ? '已自定义' : '内置默认' }}
                      </el-tag>
                      <span class="text-muted prompt-row-hint">
                        AI 的固定写作规则，改完立即生效
                      </span>
                      <span class="prompt-row-actions">
                        <el-button
                          v-if="auth.isAdmin"
                          link
                          type="primary"
                          data-testid="cfg-edit-prompt"
                          @click="promptDialogVisible = true"
                        >
                          编辑规则
                        </el-button>
                        <el-button
                          v-if="auth.isAdmin"
                          link
                          type="primary"
                          data-testid="cfg-history-prompt"
                          @click="promptHistoryVisible = true"
                        >
                          历史
                        </el-button>
                      </span>
                    </div>
                  </el-descriptions-item>
                </el-descriptions>
                <el-skeleton v-else :rows="3" animated />
              </el-card>
            </el-col>

            <el-col :span="12">
              <el-card shadow="never" class="cfg-card" data-testid="cfg-card-scheduler">
                <template #header>
                  <div class="card-head">
                    <span>定时调度</span>
                    <el-tag
                      v-if="settings"
                      :type="readyTag(settings.scheduler_running).type"
                      size="small"
                      data-testid="cfg-tag-scheduler"
                    >
                      {{ settings.scheduler_running ? '运行中' : '未运行' }}
                    </el-tag>
                  </div>
                </template>
                <el-descriptions :column="1" size="small" border v-if="settings">
                  <el-descriptions-item label="调度器开关">
                    {{ yesNo(settings.scheduler_enabled) }}
                  </el-descriptions-item>
                  <el-descriptions-item label="求值时区">
                    {{ settings.report_timezone }}
                  </el-descriptions-item>
                  <el-descriptions-item label="错过补偿宽限">
                    {{ settings.misfire_grace_hours }} 小时
                  </el-descriptions-item>
                  <el-descriptions-item label="已注册 job">
                    <span class="code-chip">
                      {{ settings.scheduler_jobs.length ? settings.scheduler_jobs.join(', ') : '—' }}
                    </span>
                  </el-descriptions-item>
                </el-descriptions>
                <el-skeleton v-else :rows="4" animated />
              </el-card>
            </el-col>

            <el-col :span="12">
              <el-card shadow="never" class="cfg-card" data-testid="cfg-card-confirm">
                <template #header><span>确认流程</span></template>
                <el-descriptions :column="1" size="small" border v-if="settings">
                  <el-descriptions-item label="确认链接有效期">
                    {{ settings.confirm_token_ttl_hours }} 小时
                  </el-descriptions-item>
                  <el-descriptions-item label="未确认提醒间隔">
                    {{ settings.reminder_interval_hours }} 小时
                  </el-descriptions-item>
                  <el-descriptions-item label="过期联系邮箱">
                    {{ settings.task_contact_email || '—' }}
                  </el-descriptions-item>
                </el-descriptions>
                <el-skeleton v-else :rows="3" animated />
              </el-card>
            </el-col>

            <el-col :span="12">
              <el-card shadow="never" class="cfg-card" data-testid="cfg-card-integrations">
                <template #header><span>其它集成</span></template>
                <el-descriptions :column="1" size="small" border v-if="settings">
                  <el-descriptions-item label="ONLYOFFICE 在线编辑">
                    <el-tag
                      :type="readyTag(settings.onlyoffice_enabled).type"
                      size="small"
                    >
                      {{ readyTag(settings.onlyoffice_enabled).text }}
                    </el-tag>
                  </el-descriptions-item>
                </el-descriptions>
                <el-skeleton v-else :rows="1" animated />
              </el-card>
            </el-col>
          </el-row>
        </div>
      </el-tab-pane>
    <!-- 用户管理 -->
      <el-tab-pane label="用户" name="users" data-testid="settings-tab-users">
        <div class="users-pane">
          <el-alert
            v-if="!auth.isAdmin"
            type="info"
            show-icon
            :closable="false"
            class="users-hint"
            title="你以普通用户身份查看，仅可浏览；新建 / 编辑 / 停用 / 删除需管理员权限"
          />
          <div class="users-toolbar">
            <el-button
              v-if="auth.isAdmin"
              type="primary"
              data-testid="user-create"
              @click="openUserCreate"
            >
              新建用户
            </el-button>
            <el-button :loading="usersLoading" data-testid="users-refresh" @click="loadUsers">
              刷新
            </el-button>
            <span class="text-muted">
              共 {{ users.length }} 个用户{{ auth.isAdmin ? '（含停用）' : '' }}，停用者不可登录
            </span>
          </div>

          <el-table
            v-loading="usersLoading"
            :data="users"
            empty-text="暂无用户"
            data-testid="users-table"
          >
            <el-table-column prop="username" label="登录名" min-width="120" />
            <el-table-column label="显示名" min-width="140">
              <template #default="{ row }">
                {{ row.display_name || '—' }}
              </template>
            </el-table-column>
            <el-table-column prop="email" label="邮箱" min-width="200" />
            <el-table-column label="角色" width="100">
              <template #default="{ row }">
                <span
                  :class="[
                    'soft-tag',
                    row.role === 'admin' ? 'soft-tag--accent' : 'soft-tag--neutral',
                  ]"
                >
                  {{ row.role === 'admin' ? '管理员' : '普通用户' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <span
                  :class="[
                    'soft-tag',
                    row.is_active ? 'soft-tag--success' : 'soft-tag--neutral',
                  ]"
                  :data-testid="`user-active-${row.id}`"
                >
                  {{ row.is_active ? '启用' : '停用' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column v-if="auth.isAdmin" label="操作" width="180" fixed="right">
              <template #default="{ row }">
                <el-button
                  link
                  type="primary"
                  :data-testid="`user-edit-${row.id}`"
                  @click="openUserEdit(row)"
                >
                  编辑
                </el-button>
                <el-button
                  link
                  type="primary"
                  :data-testid="`user-toggle-${row.id}`"
                  @click="onToggleUser(row)"
                >
                  {{ row.is_active ? '停用' : '启用' }}
                </el-button>
                <el-button
                  link
                  type="danger"
                  :data-testid="`user-delete-${row.id}`"
                  @click="onDeleteUser(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 新建用户 -->
    <FormDialog
      v-model:visible="userCreateVisible"
      title="新建用户"
      :submitting="userSaving"
      width="520px"
      @submit="saveNewUser"
    >
      <el-form label-width="90px" @submit.prevent data-testid="user-create-dialog">
        <el-form-item label="登录名" required>
          <el-input
            v-model="newUser.username"
            placeholder="字母/数字/ . _ -，2-64 位"
            data-testid="user-form-username"
          />
        </el-form-item>
        <el-form-item label="显示名">
          <el-input
            v-model="newUser.display_name"
            placeholder="留空则与登录名相同"
            data-testid="user-form-display-name"
          />
        </el-form-item>
        <el-form-item label="邮箱" required>
          <el-input v-model="newUser.email" data-testid="user-form-email" />
        </el-form-item>
        <el-form-item label="初始密码" required>
          <el-input
            v-model="newUser.password"
            type="password"
            show-password
            placeholder="至少 8位"
            data-testid="user-form-password"
          />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="newUser.role" class="full-width" data-testid="user-form-role">
            <el-option label="普通用户" value="user" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </el-form-item>
      </el-form>
    </FormDialog>

    <!-- 编辑用户 -->
    <FormDialog
      v-model:visible="userDialogVisible"
      title="编辑用户"
      :submitting="userSaving"
      width="520px"
      @submit="saveUser"
    >
      <el-form label-width="90px" @submit.prevent data-testid="user-edit-dialog">
        <el-form-item label="登录名">
          <el-input :model-value="userForm.username" disabled data-testid="user-form-username-ro" />
        </el-form-item>
        <el-form-item label="显示名" required>
          <el-input v-model="userForm.display_name" data-testid="user-form-display-name" />
        </el-form-item>
        <el-form-item label="邮箱" required>
          <el-input v-model="userForm.email" data-testid="user-form-email" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="userForm.role" class="full-width" data-testid="user-form-role">
            <el-option label="普通用户" value="user" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </el-form-item>
      </el-form>
    </FormDialog>

    <!-- 编辑邮件集成（密码不回显：留空即不修改） -->
    <FormDialog
      v-model:visible="mailDialogVisible"
      title="编辑邮件集成"
      :submitting="cfgSaving"
      width="560px"
      @submit="saveMail"
    >
      <el-form label-width="140px" @submit.prevent data-testid="mail-edit-dialog">
        <el-form-item label="SMTP 服务器" required>
          <el-input
            v-model="mailForm.smtp_host"
            placeholder="smtp.example.com"
            data-testid="mail-form-host"
          />
        </el-form-item>
        <el-form-item label="端口" required>
          <el-input-number
            v-model="mailForm.smtp_port"
            :min="1"
            :max="65535"
            controls-position="right"
            class="full-width"
            data-testid="mail-form-port"
          />
        </el-form-item>
        <el-form-item label="用户名">
          <el-input v-model="mailForm.smtp_username" data-testid="mail-form-username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="mailForm.smtp_password"
            type="password"
            show-password
            :placeholder="settings?.smtp_secret_set ? '留空则不修改当前密码' : '未设置，可留空'"
            data-testid="mail-form-password"
          />
        </el-form-item>
        <el-form-item label="发件人" required>
          <el-input
            v-model="mailForm.smtp_from"
            placeholder="no-reply@example.com"
            data-testid="mail-form-from"
          />
        </el-form-item>
        <el-form-item label="启用 TLS">
          <el-switch v-model="mailForm.smtp_use_tls" data-testid="mail-form-tls" />
        </el-form-item>
        <el-form-item label="确认链接基础地址" required>
          <el-input
            v-model="mailForm.app_base_url"
            placeholder="https://reports.example.com"
            data-testid="mail-form-base-url"
          />
        </el-form-item>
      </el-form>
    </FormDialog>

    <!-- 编辑 AI 集成（API Key 不回显：留空即不修改） -->
    <FormDialog
      v-model:visible="aiDialogVisible"
      title="编辑 AI 集成"
      :submitting="cfgSaving"
      width="520px"
      @submit="saveAi"
    >
      <el-form label-width="100px" @submit.prevent data-testid="ai-edit-dialog">
        <el-form-item label="接口地址">
          <el-input
            v-model="aiForm.ai_base_url"
            placeholder="https://api.openai.com/v1"
            data-testid="ai-form-base-url"
          />
        </el-form-item>
        <el-form-item label="AI 模型" required>
          <el-input
            v-model="aiForm.ai_model"
            placeholder="gpt-4o-mini"
            data-testid="ai-form-model"
          />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="aiForm.ai_api_key"
            type="password"
            show-password
            :placeholder="settings?.ai_secret_set ? '留空则不修改当前 Key' : '未设置，可留空'"
            data-testid="ai-form-api-key"
          />
        </el-form-item>
      </el-form>
    </FormDialog>

    <!-- 系统提示词（两段规则文本）：编辑器 + 版本历史 -->
    <AiSystemPromptDialog
      v-model="promptDialogVisible"
      @saved="loadSettings"
    />
    <AiPromptHistoryDrawer v-model="promptHistoryVisible" />
  </div>
</template>

<style scoped lang="scss">
.settings-tabs :deep(.el-tabs__header) {
  margin-bottom: var(--space-4);
}

.runtime-hint {
  margin-bottom: var(--space-4);
}

.cfg-card {
  margin-bottom: var(--space-4);
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-head-right {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.users-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.prompt-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.prompt-row-hint {
  font-size: 12px;
}

.prompt-row-actions {
  margin-left: auto;
  display: flex;
  gap: var(--space-1);
}

.users-hint {
  margin-bottom: var(--space-3);
}

.full-width {
  width: 100%;
}
</style>