import { http } from './http'

import type { UserRead } from './types'

/** 新建用户（系统设置 · 用户） */
export interface UserCreate {
  username: string
  display_name: string
  email: string
  password: string
  role: 'admin' | 'user'
  is_active: boolean
}

/** 编辑用户（系统设置 · 用户） */
export interface UserUpdate {
  display_name?: string
  email?: string
  role?: 'admin' | 'user'
  is_active?: boolean
}

export const userApi = {
  /** 默认只返回启用用户（下拉选择用） */
  list() {
    return http.get<UserRead[]>('/users')
  },
  /** 含停用用户（用户管理页，需管理员） */
  listAll() {
    return http.get<UserRead[]>('/users', { params: { include_inactive: true } })
  },
  create(payload: UserCreate) {
    return http.post<UserRead>('/users', payload)
  },
  update(id: number, payload: UserUpdate) {
    return http.patch<UserRead>(`/users/${id}`, payload)
  },
  /** 预览离职人员名下各类数据的条数（不改动任何东西） */
  ownershipSummary(id: number) {
    return http.get<Record<string, number>>(`/users/${id}/ownership-summary`)
  },
  /** 离职交接：把 from_user_id 的指标/任务/模板转给 to_user_id */
  transferOwnership(payload: {
    from_user_id: number
    to_user_id: number
    include_metrics?: boolean
    include_tasks?: boolean
    include_templates?: boolean
    delete_grants?: boolean
  }) {
    return http.post<{
      metrics: number
      tasks: number
      templates: number
      grants_deleted: number
      transfer_id: number
    }>('/users/transfer-ownership', payload)
  },
  remove(id: number) {
    return http.delete<void>(`/users/${id}`)
  },
}
