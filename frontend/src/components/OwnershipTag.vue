<script setup lang="ts">
/**
 * 归属标记（角色组共享圈，2026-11-23）。
 *
 * 显示「我的」/ 角色组名（多个折叠成 +N，tooltip 给全量）/「他人」，
 * 只读（角色组共享来的）额外挂一个「只读」标签。
 *
 * **不只靠颜色区分**（可访问性）：每种归属都有对应文字。
 * 后端还没下发 `ownership` 字段时按「我的」处理（= 旧行为，不报错）。
 */
import { computed } from 'vue'

import type { OwnershipInfo } from '@/api/types'
import { ownershipView } from '@/utils/ownership'

const props = withDefaults(
  defineProps<{
    /** 后端下发的归属信息；缺省 = 按「自己的」处理 */
    ownership?: OwnershipInfo | null
    /** 折叠前最多展示几个角色组名 */
    limit?: number
    /**
     * 后端**没下发** ownership 时的兜底「是不是我的」。
     *
     * 后端对 admin 不下发 ownership（admin 可见全部，没有「共享自哪个角色组」
     * 的概念），此时由页面层按 owner_id / created_by 是不是我来判定，
     * 否则 admin 看到的别人的资源也会被标成「我的」。
     */
    fallbackIsOwn?: boolean
    /**
     * 「既不是自己的、也不在任何角色组里」时的文案。
     *
     * admin 看到的是**别人**的资源 -> 「他人」；
     * 普通用户能看到别人的资源只可能是**共享**来的 -> 「共享」。
     */
    othersLabel?: string
  }>(),
  { ownership: null, limit: 2, fallbackIsOwn: true, othersLabel: '他人' },
)

const view = computed(() =>
  ownershipView(
    { ownership: props.ownership },
    { limit: props.limit, fallbackIsOwn: props.fallbackIsOwn },
  ),
)
</script>

<template>
  <div class="ownership-cell">
    <span v-if="view.isOwn" class="soft-tag soft-tag--accent">我的</span>
    <template v-else>
      <span v-for="r in view.shown" :key="r" class="soft-tag soft-tag--info">{{ r }}</span>
      <el-tooltip v-if="view.rest > 0" :content="view.all.join('、')" placement="top">
        <span class="soft-tag soft-tag--info">+{{ view.rest }}</span>
      </el-tooltip>
      <span v-if="view.others" class="soft-tag soft-tag--neutral">{{ othersLabel }}</span>
    </template>
    <span v-if="view.readOnly" class="soft-tag soft-tag--warning">只读</span>
  </div>
</template>

<style scoped lang="scss">
.ownership-cell {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-1);
}
</style>
