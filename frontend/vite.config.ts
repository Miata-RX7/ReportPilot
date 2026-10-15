/// <reference types="vitest" />
import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  css: {
    preprocessorOptions: {
      scss: {
        // 全局注入 Element Plus 主题覆盖（Design Token §8）
        additionalData: `@use "@/styles/element-override.scss" as *;`,
        api: 'modern-compiler',
        silenceDeprecations: ['legacy-js-api'],
      },
    },
  },
  server: {
    port: 5173,
    // 开发期把 /api 代理到后端，避免 CORS
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'node',
    globals: true,
    include: ['src/**/*.spec.ts'],
    setupFiles: ['./src/__tests__/setup.ts'],
  },
})
