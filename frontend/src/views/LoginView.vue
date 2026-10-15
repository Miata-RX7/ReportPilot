<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'

import { useAuthStore } from '@/stores/auth'
import { parseApiError } from '@/utils/error'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ username: '', password: '' })

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

/** 消费 401 跳转带来的 redirect 参数；只接受站内路径，防开放重定向 */
function resolveRedirect(): string {
  const redirect = route.query.redirect
  if (typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')) {
    return redirect
  }
  return '/datasources'
}

async function onSubmit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await auth.login(form.username, form.password)
    ElMessage.success('登录成功')
    await router.replace(resolveRedirect())
  } catch (error) {
    ElMessage.error(parseApiError(error).message)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <el-card class="login-card">
      <div class="login-title">ReportPilot</div>
      <div class="login-subtitle text-muted">月报自动化生成系统</div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @keyup.enter="onSubmit"
      >
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="请输入用户名" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="请输入密码"
            autocomplete="current-password"
          />
        </el-form-item>
        <el-button type="primary" class="login-button" :loading="loading" @click="onSubmit">
          登录
        </el-button>
      </el-form>

      <div class="login-hint text-muted">
        账号由管理员通过 CLI 创建：<code>python -m app.cli create-user</code>
      </div>
    </el-card>
  </div>
</template>

<style scoped lang="scss">
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background: linear-gradient(135deg, var(--color-primary-soft) 0%, var(--color-bg) 100%);
}

.login-card {
  width: var(--layout-login-card-width);
  padding: var(--space-2) var(--space-3);
}

.login-title {
  font-size: var(--font-xl);
  font-weight: 600;
  text-align: center;
  color: var(--color-primary);
}

.login-subtitle {
  text-align: center;
  margin: var(--space-2) 0 var(--space-5);
  font-size: var(--font-sm);
}

.login-button {
  width: 100%;
  margin-top: var(--space-1);
}

.login-hint {
  margin-top: var(--space-4);
  font-size: var(--font-xs);
  text-align: center;
  line-height: 1.6;
}
</style>
