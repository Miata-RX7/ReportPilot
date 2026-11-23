import { http } from './http'

import type { RoleMemberRead, RoleRead } from './types'

/**
 * 角色组（共享圈）接口（2026-11-23）。
 *
 * 「角色组」= 一组人互相可见对方的指标 / 报告任务（**只读**），
 * 与「用户权限」（users.role 的 admin/user 两档）是两回事，别混用。
 *
 * 可见性（后端保证）：
 * - **admin**：全部角色
 * - **普通用户**：只返回**自己加入的**角色
 */
export interface RoleCreate {
  name: string
  description?: string
}

/** 改名 / 改描述；不传的字段保持原值 */
export interface RoleUpdate {
  name?: string
  description?: string
}

export const roleApi = {
  /** 角色列表：admin 见全部；普通用户只见自己加入的 */
  list() {
    return http.get<RoleRead[]>('/roles')
  },

  /** 新建角色组（admin） */
  create(payload: RoleCreate) {
    return http.post<RoleRead>('/roles', payload)
  },

  /** 改名 / 改描述（admin） */
  update(id: number, payload: RoleUpdate) {
    return http.patch<RoleRead>(`/roles/${id}`, payload)
  },

  /** 删角色（admin）：成员关系级联删除，共享关系随之消失 */
  remove(id: number) {
    return http.delete<void>(`/roles/${id}`)
  },

  /** 成员列表；普通用户只能看自己加入的角色的成员 */
  members(id: number) {
    return http.get<RoleMemberRead[]>(`/roles/${id}/members`)
  },

  /** 加成员（admin，可批量）；返回加完后的完整成员列表 */
  addMembers(id: number, userIds: number[]) {
    return http.post<RoleMemberRead[]>(`/roles/${id}/members`, { user_ids: userIds })
  },

  /** 移成员（admin） */
  removeMember(id: number, userId: number) {
    return http.delete<void>(`/roles/${id}/members/${userId}`)
  },
}
