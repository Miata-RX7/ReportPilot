<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { templateApi } from '@/api/templates'
import { parseApiError } from '@/utils/error'

const props = withDefaults(
  defineProps<{
    templateId: number
    mode?: 'view' | 'edit'
    /** 保存回调（编辑模式下 DS 请求关闭时触发） */
    reloadOnClose?: boolean
  }>(),
  { mode: 'view', reloadOnClose: false },
)

const emit = defineEmits<{
  (e: 'disabled', reason: string): void
  (e: 'ready'): void
  (e: 'saved'): void
  (e: 'error', message: string): void
}>()

let seq = 0
const uid = ++seq + '-' + Math.random().toString(36).slice(2, 7)
const hostId = `onlyoffice-host-${uid}`
const loading = ref(true)
let editor: { destroyEditor?: () => void } | null = null

/**
 * ONLYOFFICE api.js 的**全局共享加载**。
 *
 * 原来的实现有个竞态（2026-11-25 生产报错
 * `Cannot read properties of undefined (reading 'DocEditor')`）：
 *
 *   if (document.querySelector(`script[src="${src}"]`)) { resolve(); return }
 *
 * 看到 <script> 标签就认为「已加载」-> resolve()，但脚本**可能还在下载中**
 * （api.js 要经 DS 的 302 重定向到版本化路径，首次很慢）。这段时间里组件被
 * 重新挂载 -> 第二个组件拿到一个「假的成功」-> `window.DocsAPI` 还没定义
 * -> `new DocsAPI.DocEditor(...)` 直接抛 TypeError。
 *
 * 「过一会就好了」正是这个原因：等 api.js 真的加载完并缓存，再打开就正常。
 *
 * 修法：
 * - 用**模块级共享 Promise**：脚本只加载一次，所有调用者等同一个 Promise
 * - onload 后**检查 window.DocsAPI 是否真的存在**
 * - 失败时移除 script 标签并清空 Promise -> **允许下次重试**
 *   （原来失败后标签残留 -> 之后每次都「假装成功」-> 永久失败到刷新页面）
 */
let apiJsPromise: Promise<void> | null = null
let apiJsReady = false

/** api.js 加载超时（毫秒）—— 避免 Promise 永久挂起 */
const API_JS_TIMEOUT_MS = 30_000

function loadScriptOnce(src: string): Promise<void> {
  const w = window as unknown as { DocsAPI?: unknown }
  if (apiJsReady && w.DocsAPI) return Promise.resolve()
  if (apiJsPromise) return apiJsPromise

  apiJsPromise = new Promise<void>((resolve, reject) => {
    const s = document.createElement('script')
    s.src = src
    s.async = true
    s.dataset.onlyofficeApi = '1'

    const cleanup = () => {
      s.remove()
      apiJsPromise = null
    }
    const timer = setTimeout(() => {
      cleanup()
      reject(new Error('ONLYOFFICE api.js 加载超时'))
    }, API_JS_TIMEOUT_MS)

    s.onload = () => {
      clearTimeout(timer)
      if (w.DocsAPI) {
        apiJsReady = true
        resolve()
      } else {
        cleanup()
        reject(new Error('ONLYOFFICE api.js 已加载但 DocsAPI 未就绪'))
      }
    }
    s.onerror = () => {
      clearTimeout(timer)
      cleanup()
      reject(new Error('ONLYOFFICE api.js 加载失败'))
    }
    document.head.appendChild(s)
  })
  return apiJsPromise
}

async function mount() {
  loading.value = true
  try {
    const { data } = await templateApi.editor(props.templateId, props.mode)
    if (!data.enabled) {
      emit(
        'disabled',
        '未配置 ONLYOFFICE Document Server。请在 .env 设置 ONLYOFFICE_URL / ONLYOFFICE_JWT_SECRET / PUBLIC_BASE_URL。',
      )
      return
    }
    await loadScriptOnce(
      `${data.docserver_url.replace(/\/$/, '')}/web-apps/apps/api/documents/api.js`,
    )
    const cfg: Record<string, unknown> = { ...data.config }
    cfg.events = {
      onDocumentReady: () => {
        loading.value = false
        emit('ready')
      },
      onError: (e: { data?: { errorDescription?: string; errorCode?: number } }) => {
        const d = e?.data ?? {}
        emit('error', `编辑器错误: ${d.errorDescription || d.errorCode || ''}`)
      },
      onRequestClose: () => {
        if (props.reloadOnClose) emit('saved')
      },
    }
    const w = window as unknown as {
      DocsAPI?: { DocEditor: new (id: string, cfg: unknown) => { destroyEditor?: () => void } }
    }
    // 判空：没有它会抛 `Cannot read properties of undefined (reading 'DocEditor')`
    // —— 那是个**误导性**报错，真正原因是 api.js 没就绪。
    if (!w.DocsAPI) {
      throw new Error('ONLYOFFICE 脚本未就绪（window.DocsAPI 不存在），请重新打开')
    }
    editor = new w.DocsAPI.DocEditor(hostId, cfg)
  } catch (error) {
    emit('error', parseApiError(error).message || '打开 ONLYOFFICE 失败')
    loading.value = false
  }
}

onMounted(mount)
onBeforeUnmount(() => {
  try {
    editor?.destroyEditor?.()
  } catch {
    /* ignore */
  }
  editor = null
})
</script>

<template>
  <div v-loading="loading" class="oo-embed">
    <div :id="hostId" class="oo-frame" data-testid="onlyoffice-frame" />
  </div>
</template>

<style scoped lang="scss">
.oo-embed {
  width: 100%;
  height: 100%;
}

.oo-frame {
  width: 100%;
  height: 100%;
}
</style>
