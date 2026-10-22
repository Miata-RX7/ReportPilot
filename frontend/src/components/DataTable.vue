<script setup lang="ts">
/**
 * 列表页统一表格容器。
 * slots:
 *  - actions：表格右上角工具条（可选）
 *  - columns：el-table-column 列表（含行内操作列）
 *
 * 选择（selection）两种模式：
 *  - selectionHeader=true：表头带「勾选全部」框。**它只作用于当前页**——
 *    因为本项目的分页是前端切片，:data 里本来就只有本页数据，
 *    表头勾选自然不会跨页（用户 2026-10-22 明确要求）。
 *  - selectionHeader=false：表头不显示勾选框，**只能逐行手动勾选**
 *    （模板管理用这个模式）。
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
    /** 是否显示选择列 */
    selection?: boolean
    /** 表头是否显示「勾选全部」（false = 只能手动勾选） */
    selectionHeader?: boolean
    /** 行的唯一键；配合 selection 使用 */
    rowKey?: string
    /** 某一行是否允许勾选 */
    selectable?: (row: any, index: number) => boolean
  }>(),
  {
    loading: false,
    total: 0,
    pagination: false,
    page: 1,
    pageSize: 10,
    emptyText: '暂无数据',
    selection: false,
    selectionHeader: true,
    rowKey: undefined,
    selectable: undefined,
  },
)

const emit = defineEmits<{
  (e: 'update:page', value: number): void
  (e: 'update:pageSize', value: number): void
  (e: 'selection-change', rows: any[]): void
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
        :row-key="rowKey"
        :class="{ 'data-table--no-select-all': selection && !selectionHeader }"
        :row-style="{ height: 'var(--table-row-height)' }"
        :header-cell-style="{
          backgroundColor: 'var(--table-header-bg)',
          color: 'var(--table-text)',
        }"
        @selection-change="(rows: any[]) => emit('selection-change', rows)"
      >
        <el-table-column
          v-if="selection"
          type="selection"
          width="46"
          :selectable="selectable"
        />
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

/*
 * 「只能手动勾选」模式：隐藏表头的勾选全部框。
 * 用 visibility 而不是 display —— 保留列宽与对齐，行内勾选框位置不变。
 */
.data-table--no-select-all :deep(th.el-table-column--selection .el-checkbox) {
  visibility: hidden;
}
</style>
