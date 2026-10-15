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

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) {
      resolve()
      return
    }
    const s = document.createElement('script')
    s.src = src
    s.onload = () => resolve()
    s.onerror = () => reject(new Error('ONLYOFFICE api.js 加载失败'))
    document.head.appendChild(s)
  })
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
    await loadScript(`${data.docserver_url.replace(/\/$/, '')}/web-apps/apps/api/documents/api.js`)
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
      DocsAPI: { DocEditor: new (id: string, cfg: unknown) => { destroyEditor?: () => void } }
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
