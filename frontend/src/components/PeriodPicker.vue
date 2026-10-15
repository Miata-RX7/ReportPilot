<script setup lang="ts">
/**
 * 统计周期选择器（月 / ISO 周双粒度）。
 *
 * 值恒为后端契约的周期编码：月 `YYYY-MM`、周 `YYYY-Www`（ISO 周，周一 ~ 周日）。
 *
 * 为什么周模式用「日期选择器 + 归到 ISO 周」而不是 el-date-picker 的 `type="week"`：
 * Element Plus 的周选择器用的是**本地化周**（周起始日与周号由 locale 决定，英文默认
 * 周日，周号也不是 ISO 周），与后端 `period_utils` 的 ISO 口径对不上，会系统性错位
 * 一天。因此这里让用户选周内任意一天，再由 `weekOf()` 归一到 ISO 周编码。
 *
 * 切换粒度时跳到新粒度的「上一个完整周期」（月=上月、周=上周）：周跨月/跨年时
 * 「它属于哪个月」本身就有歧义，不做跨粒度换算。
 */
import { computed } from 'vue'

import {
  isWeekPeriod,
  parseDateString,
  periodLabel,
  previousPeriod,
  previousWeek,
  toDateString,
  weekOf,
  weekStartDate,
} from '@/utils/format'

const props = withDefaults(
  defineProps<{
    modelValue: string
    disabled?: boolean
    testid?: string
  }>(),
  { disabled: false, testid: 'period-picker' },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const isWeek = computed(() => isWeekPeriod(props.modelValue))

/** 月选择器绑定值（仅月模式渲染；周周期时给上月占位，不参与提交） */
const monthValue = computed(() => (isWeek.value ? previousPeriod() : props.modelValue))

/** 周模式绑定的日期：取该 ISO 周的周一（仅周模式渲染） */
const weekDate = computed(() => {
  const monday = weekStartDate(props.modelValue) ?? weekStartDate(previousWeek())
  return toDateString(monday ?? new Date())
})

function onModeChange(next: unknown) {
  if (next === 'week') emit('update:modelValue', previousWeek())
  else if (next === 'month') emit('update:modelValue', previousPeriod())
}

function onMonthChange(value: unknown) {
  if (typeof value === 'string' && value) emit('update:modelValue', value)
}

function onWeekChange(value: unknown) {
  if (typeof value !== 'string') return
  const date = parseDateString(value)
  if (date) emit('update:modelValue', weekOf(date))
}
</script>

<template>
  <div class="period-picker" :data-testid="testid">
    <el-radio-group
      :model-value="isWeek ? 'week' : 'month'"
      size="small"
      :disabled="disabled"
      data-testid="period-picker-mode"
      @change="onModeChange"
    >
      <el-radio-button value="month">月</el-radio-button>
      <el-radio-button value="week">周</el-radio-button>
    </el-radio-group>

    <el-date-picker
      v-if="!isWeek"
      :model-value="monthValue"
      type="month"
      value-format="YYYY-MM"
      placeholder="选择月份"
      :clearable="false"
      :disabled="disabled"
      class="period-picker__input"
      data-testid="period-picker-month"
      @update:model-value="onMonthChange"
    />
    <el-date-picker
      v-else
      :model-value="weekDate"
      type="date"
      value-format="YYYY-MM-DD"
      placeholder="选择周内任一天"
      :clearable="false"
      :disabled="disabled"
      class="period-picker__input"
      data-testid="period-picker-week"
      @update:model-value="onWeekChange"
    />

    <span v-if="isWeek" class="period-picker__hint text-muted">
      {{ periodLabel(modelValue) }}
    </span>
  </div>
</template>

<style scoped lang="scss">
.period-picker {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.period-picker__input {
  width: 150px;
  flex: none;
}

.period-picker__hint {
  font-size: var(--font-xs);
  white-space: nowrap;
}
</style>