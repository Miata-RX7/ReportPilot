<script setup lang="ts">
/**
 * 列表页统一表格容器。
 * slots:
 *  - actions：表格右上角工具条（可选）
 *  - columns：el-table-column 列表（含行内操作列）
 * 分页 Phase 1 不启用（后端返回全量），保留 props 供后续使用。
 */
withDefaults(
  defineProps<{
    // el-table 行数据类型交由调用方决定（列表页实体各异）
    data: any[]
    loading?: boolean
    total?: number
    pagination?: boolean
    page?: number
    pageSize?: number
    emptyText?: string
  }>(),
  {
    loading: false,
    total: 0,
    pagination: false,
    page: 1,
    pageSize: 10,
    emptyText: '暂无数据',
  },
)

const emit = defineEmits<{
  (e: 'update:page', value: number): void
  (e: 'update:pageSize', value: number): void
}>()
</script>

<template>
  <div class="data-table">
    <div v-if="$slots.actions" class="data-table__toolbar">
      <slot name="actions" />
    </div>

    <el-card shadow="never" class="data-table__card">
      <el-table
        v-loading="loading"
        :data="data"
        :row-style="{ height: 'var(--table-row-height)' }"
        :header-cell-style="{
          backgroundColor: 'var(--table-header-bg)',
          color: 'var(--table-text)',
        }"
      >
        <slot name="columns" />
        <template #empty>
          <el-empty :description="emptyText" />
        </template>
      </el-table>

      <div v-if="pagination" class="data-table__pager">
        <el-pagination
          layout="total, sizes, prev, pager, next, jumper"
          :total="total"
          :current-page="page"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          @current-change="(v: number) => emit('update:page', v)"
          @size-change="(v: number) => emit('update:pageSize', v)"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped lang="scss">
.data-table__toolbar {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.data-table__card {
  border-radius: var(--card-radius);
  box-shadow: var(--card-shadow);
}

.data-table__pager {
  display: flex;
  justify-content: flex-end;
  padding: var(--space-3) var(--space-4);
}
</style>
