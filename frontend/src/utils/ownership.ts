import type { OwnershipInfo } from '@/api/types'

/**
 * 归属（角色组共享圈）的展示逻辑（2026-11-23）。
 *
 * 后端在 `MetricListItem` / `ReportTaskRead` 上挂一个 `ownership` 字段：
 * ```
 * ownership?: {
 *   is_own: boolean
 *   shared_by_roles: string[]
 *   read_only: boolean
 * }
 * ```
 *
 * **后端还没上线的阶段整个字段可能不存在** —— 所以消费端一律走
 * `ownershipOf()`，它把缺省值补齐，绝不让页面因为字段缺失而报错。
 */

/** 字段缺失时的兜底：既当自己的、也不只读（= 旧行为） */
export const EMPTY_OWNERSHIP: OwnershipInfo = {
  is_own: true,
  shared_by_roles: [],
  read_only: false,
}

/** 读取归属信息并补齐缺省字段（字段不存在 / 为 null / 内部字段缺失都不报错） */
export function ownershipOf(
  row: { ownership?: OwnershipInfo | null } | null | undefined,
): OwnershipInfo {
  const o = row?.ownership
  if (!o) return { ...EMPTY_OWNERSHIP }
  return {
    is_own: o.is_own ?? false,
    shared_by_roles: Array.isArray(o.shared_by_roles)
      ? o.shared_by_roles.filter((x): x is string => typeof x === 'string' && x !== '')
      : [],
    read_only: o.read_only ?? false,
  }
}

/** 归属列的展示模型 */
export interface OwnershipView {
  /** 是否是自己的资源 */
  isOwn: boolean
  /** 只读（同角色组共享来的）—— 编辑 / 删除要禁用 */
  readOnly: boolean
  /** 折叠后要展示的角色组名 */
  shown: string[]
  /** 多出来的角色组数（0 = 全部都展示了） */
  rest: number
  /** 全部角色组名（tooltip 用） */
  all: string[]
  /** 既不是自己的、也不在任何角色组里（只有 admin 能看到别人的资源） */
  others: boolean
}

/** 归属列的展示选项 */
export interface OwnershipViewOptions {
  /** 折叠前最多展示几个角色组名（默认 2） */
  limit?: number
  /**
   * 后端**没有下发** ownership 时的兜底「是不是我的」。
   *
   * 为什么需要它：后端对 admin 不下发 ownership（admin 可见全部、资源没有
   * 「共享自哪个角色组」的概念），此时若一律按「我的」处理，admin 看到的
   * 别人的资源也会标成「我的」。所以由**知道当前用户是谁**的页面层把
   * 「owner_id / created_by 是不是我」算好传进来。
   */
  fallbackIsOwn?: boolean
}

/**
 * 归属列的展示：
 * - 自己的 -> 「我的」
 * - 角色组共享来的 -> 角色组名（超过 limit 个折叠成 +N，tooltip 里给全量）
 * - 其它（仅 admin 看得到别人的资源）-> 「他人」
 *
 * 文字与颜色并用（不只靠颜色区分），保证可访问性。
 */
export function ownershipView(
  row: { ownership?: OwnershipInfo | null } | null | undefined,
  opts: OwnershipViewOptions = {},
): OwnershipView {
  const limit = opts.limit ?? 2
  const o = row?.ownership
  const info: OwnershipInfo = o
    ? {
        is_own: o.is_own ?? false,
        shared_by_roles: Array.isArray(o.shared_by_roles)
          ? o.shared_by_roles.filter((x): x is string => typeof x === 'string' && x !== '')
          : [],
        read_only: o.read_only ?? false,
      }
    : { ...EMPTY_OWNERSHIP, is_own: opts.fallbackIsOwn ?? true }
  const all = info.shared_by_roles
  const isOwn = info.is_own === true
  const readOnly = info.read_only === true
  if (isOwn) {
    return { isOwn, readOnly, shown: [], rest: 0, all: [], others: false }
  }
  if (all.length === 0) {
    return { isOwn, readOnly, shown: [], rest: 0, all: [], others: true }
  }
  return {
    isOwn,
    readOnly,
    shown: all.slice(0, limit),
    rest: Math.max(0, all.length - limit),
    all,
    others: false,
  }
}

/** 只读提示文案（前端唯一出处，避免各处写歪） */
export const READ_ONLY_TOOLTIP = '角色组共享（只读），仅属主与管理员可修改'

/** 指标 / 报告任务列表的「范围」筛选值（对应后端 query 参数 scope） */
export type OwnershipScope = 'all' | 'mine' | 'shared'

/** 范围筛选项 */
export const SCOPE_OPTIONS: { value: OwnershipScope; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'mine', label: '仅我的' },
  { value: 'shared', label: '角色组共享的' },
]
