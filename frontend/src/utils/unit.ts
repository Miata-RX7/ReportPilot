/**
 * 单位换算的前端镜像（对齐后端 backend/app/services/unit_scale.py，两侧各有单测锁定）。
 *
 * 职责与「数值变换」严格区分：transform 改的是**事实层数值**（参与环比与显著波动
 * 阈值判定）；unit_config 只改展示口径 —— 把 262468.9041666667 bps 展示成 262.47 Kbps。
 *
 * 报告产物的权威口径始终在后端；这里只服务页面展示与表单即时预览。
 */
import type { UnitScaleConfig } from '@/api/types'

export const PREFIXES_1000 = ['', 'K', 'M', 'G', 'T', 'P', 'E']
export const PREFIXES_1024 = ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei']
/** 换算开启但指标未配 display_precision 时的展示小数位（与后端一致） */
export const DEFAULT_DISPLAY_DECIMALS = 2
/** 紧贴数值的单位符号（与后端 preview_service.TIGHT_UNITS 一致） */
const TIGHT_UNITS = ['%', '‰', '°', '°C', '℃']

export interface ResolvedScale {
  mode: 'auto' | 'fixed'
  /** 除数（> 0） */
  factor: number
  /** 展示单位（可能为空 = 未提供单位） */
  unit: string
  /** 展示小数位 */
  precision: number
}

/**
 * 按量级选档：|v| < base -> 0 档（原单位），否则逐级上升并封顶 E 档。
 * 用循环而非 Math.log —— 浮点误差会让 1e9/1000 的幂次算成 2（与后端同因同解）。
 */
export function autoOrder(magnitude: number, base: number): number {
  const m = Math.abs(magnitude)
  if (!Number.isFinite(m)) return 0
  let order = 0
  let limit = base
  while (order < PREFIXES_1000.length - 1 && m >= limit) {
    order += 1
    limit *= base
  }
  return order
}

/**
 * 解析换算配置；未开启或配置非法返回 null（= 不换算，行为与升级前一致）。
 *
 * @param magnitude 选档依据：scalar 传本期值，curve 传全部点的最大绝对值
 */
export function resolveScale(
  config: UnitScaleConfig | null | undefined,
  opts: { magnitude: number; baseUnit?: string; precision?: number | null },
): ResolvedScale | null {
  if (!config || (config.mode !== 'auto' && config.mode !== 'fixed')) return null
  const decimals = typeof config.decimals === 'number' ? config.decimals : null
  const precision =
    decimals ?? (typeof opts.precision === 'number' ? opts.precision : DEFAULT_DISPLAY_DECIMALS)
  const baseUnit = (opts.baseUnit ?? '').trim()

  if (config.mode === 'fixed') {
    const divisor = Number(config.divisor)
    if (!Number.isFinite(divisor) || divisor <= 0) return null
    return {
      mode: 'fixed',
      factor: divisor,
      unit: (config.display_unit ?? '').trim() || baseUnit,
      precision,
    }
  }

  const base = config.base === 1024 ? 1024 : 1000
  const order = autoOrder(opts.magnitude, base)
  const prefixes = base === 1024 ? PREFIXES_1024 : PREFIXES_1000
  return {
    mode: 'auto',
    factor: base ** order,
    // 后缀优先用配置里的（bps -> Kbps），缺省回退指标原始单位
    unit: `${prefixes[order]}${(config.suffix_unit ?? '').trim() || baseUnit}`,
    precision,
  }
}

/** 缩放单个值；scale 为 null 时原样返回（供消费端统一调用） */
export function scaleValue(value: number | null, scale: ResolvedScale | null): number | null {
  if (value === null || scale === null) return value
  return value / scale.factor
}

/** 展示单位：未换算或换算后无单位时回退原始单位 */
export function displayUnitOf(scale: ResolvedScale | null, baseUnit: string): string {
  if (!scale || !scale.unit) return baseUnit
  return scale.unit
}

/** 数值 + 单位拼接：符号类单位紧贴数值（95.20%），其余单位空格分隔（262.47 Kbps） */
export function joinValueUnit(valueText: string, unit: string | null | undefined): string {
  const u = (unit ?? '').trim()
  if (!u) return valueText
  return TIGHT_UNITS.includes(u) ? `${valueText}${u}` : `${valueText} ${u}`
}

/** 取换算配置里"关闭"的落库值（表单关闭开关时提交它） */
export function disabledUnitConfig(): UnitScaleConfig {
  return { mode: 'none' }
}