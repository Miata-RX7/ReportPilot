/**
 * ECharts 按需注册 + 轻量 composable。
 *
 * 只注册用到的图表/组件，避免把整个 echarts 打进包：
 *   echarts/core + LineChart/PieChart + Grid/Tooltip/Legend + CanvasRenderer
 * 不引入 vue-echarts（少一个依赖，也少一层版本耦合）。
 */
import * as echarts from 'echarts/core'
import { LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { onBeforeUnmount, onMounted, watch, type Ref } from 'vue'

echarts.use([
  LineChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
])

export { echarts }

/**
 * 在 el 上挂载/更新图表，元素尺寸变化与 option 变化都会自动处理。
 * @param elRef 容器 ref（v-if 切换时需保证 el 存在，故用 watch immediate）
 */
export function useChart(elRef: Ref<HTMLElement | undefined | null>, option: Ref<unknown>) {
  let chart: echarts.ECharts | null = null
  let observer: ResizeObserver | null = null
  /** chart 当前绑定的 DOM 元素。v-if 分支切换时元素会被换掉，必须重绑 */
  let boundEl: HTMLElement | null = null

  function render() {
    const el = elRef.value
    if (!el || !option.value) return
    if (!chart || chart.isDisposed() || boundEl !== el) {
      // 元素被换掉（重新渲染 / destroy-on-close 后重建）：旧实例画在已卸载的
      // 节点上，必须 dispose 后对新元素重新 init，否则新的容器里没有 canvas
      chart?.dispose()
      observer?.disconnect()
      chart = echarts.init(el)
      boundEl = el
      observe(el)
    }
    chart.setOption(option.value as never, true)
    chart.resize()
  }

  /**
   * 监听容器尺寸变化并 resize 画布。
   * 必须有：ECharts 在 init 时按当时的容器宽度锁定 canvas 尺寸，之后容器变宽
   * （如试算抽屉从 480px 切到 760px）不会自动重绘，表现为图表被压扁/空白。
   */
  function observe(el: HTMLElement) {
    observer?.disconnect()
    if (typeof ResizeObserver === 'undefined') return
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(el)
  }

  function resize() {
    chart?.resize()
  }

  onMounted(() => {
    render()
    window.addEventListener('resize', resize)
  })

  // flush:'post' —— 图表容器是 v-if/v-else-if 渲染的，默认 'pre' 会在 DOM
  // 还没挂上 el 时就触发，导致 init 拿不到容器而永不渲染
  watch(option, () => render(), { deep: true, flush: 'post' })

  onBeforeUnmount(() => {
    window.removeEventListener('resize', resize)
    observer?.disconnect()
    observer = null
    chart?.dispose()
    chart = null
    boundEl = null
  })

  return { chart, render, resize }
}
