import type { HostGroupRef, HostRef, ItemRef } from '@/api/types'

/**
 * AIOps 浏览类数据的**会话内缓存**。
 *
 * 为什么必须放在独立模块而不是 `<script setup>` 里：`<script setup>` 的顶层代码
 * 跑在 `setup()` 内部，写 `const cache = new Map()` 会**每个组件实例新建一个空
 * Map**—— 弹窗每次打开都是新实例，缓存永远不命中，等于没加（这个坑踩过一次）。
 * 要真正跨实例共享，声明必须在模块作用域。
 *
 * 只缓存只读且慢的三级（模型 / 实例 / 监控项）。主键行会变，且后端已有 60s 缓存，
 * 不在这里重复缓存。
 *
 * 上限 64 条：实例增删不频繁，但长时间会话下防止无限增长。
 */

const MAX_ENTRIES = 64

export const aiopsModelsCache = new Map<string, HostGroupRef[]>()
export const aiopsInstancesCache = new Map<string, HostRef[]>()
export const aiopsItemsCache = new Map<string, ItemRef[]>()

function setCapped<T>(cache: Map<string, T>, key: string, value: T): void {
  if (cache.size >= MAX_ENTRIES) cache.clear()
  cache.set(key, value)
}

export function cacheAiopsModels(dsId: number, data: HostGroupRef[]): void {
  setCapped(aiopsModelsCache, String(dsId), data)
}

export function cacheAiopsInstances(key: string, data: HostRef[]): void {
  setCapped(aiopsInstancesCache, key, data)
}

export function cacheAiopsItems(key: string, data: ItemRef[]): void {
  setCapped(aiopsItemsCache, key, data)
}

/** 数据源配置变更 / 排查问题时清空。 */
export function clearAiopsBrowseCache(): void {
  aiopsModelsCache.clear()
  aiopsInstancesCache.clear()
  aiopsItemsCache.clear()
}