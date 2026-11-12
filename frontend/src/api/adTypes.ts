/** AD / LDAP 对接的配置与操作（2026-11-12）。 */

export interface AdSettings {
  /** AD 服务器地址，如 ldap://ldap.livecom.ltd:389 */
  server: string
  /** 绑定用的服务账号（用于搜索用户） */
  bind_dn: string
  /** 服务账号密码是否已配置（接口永不返回密码本身） */
  bind_password_set: boolean
  /** 搜索用户的根 DN */
  base_dn: string
  /** 拉取用户列表的过滤器 */
  user_filter: string
  /** 字段映射：登录名 */
  map_username: string
  /** 字段映射：显示名 */
  map_display_name: string
  /** 字段映射：邮箱 */
  map_email: string
  /** 是否启用自动同步 */
  sync_enabled: boolean
  /** 自动同步间隔（分钟） */
  sync_interval_minutes: number
  /** AD 连接 / 查询超时（秒） */
  timeout_seconds: number
  /** 四项必填是否齐全 */
  configured: boolean
}

export interface AdSettingsUpdate {
  server: string
  bind_dn: string
  /** 留空 = 不修改已存密码 */
  bind_password?: string
  base_dn: string
  user_filter: string
  map_username: string
  map_display_name: string
  map_email: string
  sync_enabled: boolean
  sync_interval_minutes: number
  timeout_seconds: number
}

export interface AdTestResult {
  ok: boolean
  message: string
  sample: {
    username: string
    display_name: string
    email: string
    disabled: boolean
  } | null
}

export interface AdSyncResult {
  total: number
  created: number
  updated: number
  disabled: number
  reactivated: number
  vanished: number
  skipped_no_email: number
  /** AD 里已禁用、首次建档时跳过的账号数 */
  skipped_disabled: number
  errors: string[]
}
