import { http } from './http'

/** 运行时配置概览（不含任何密钥） */
export interface SystemSettings {
  // 邮件
  smtp_host: string
  smtp_port: number
  smtp_from: string
  /** 账号名非密钥，用于编辑预填 */
  smtp_username: string
  smtp_use_tls: boolean
  /** host + 发件人 + 密码齐全才为 true */
  smtp_ready: boolean
  /** 密码是否已配置；密码本身绝不返回 */
  smtp_secret_set: boolean
  // 确认流程
  app_base_url: string
  confirm_token_ttl_hours: number
  reminder_interval_hours: number
  task_contact_email: string
  // 调度
  scheduler_enabled: boolean
  scheduler_running: boolean
  scheduler_jobs: string[]
  report_timezone: string
  misfire_grace_hours: number
  // 其它集成
  onlyoffice_enabled: boolean
  // AI
  ai_base_url: string
  ai_model: string
  ai_ready: boolean
  /** API Key 是否已配置；Key 本身绝不返回 */
  ai_secret_set: boolean
  /** 两段系统提示词是否已被自定义（false = 用内置默认） */
  ai_prompt_custom: boolean
}

/** 邮件集成保存载荷；smtp_password 留空 / 不传 = 保持原密码 */
export interface MailSettingsUpdate {
  smtp_host: string
  smtp_port: number
  smtp_username: string
  smtp_from: string
  smtp_use_tls: boolean
  app_base_url: string
  smtp_password?: string | null
}

/** AI 集成保存载荷；ai_api_key 留空 / 不传 = 保持原 Key */
export interface AiSettingsUpdate {
  ai_base_url: string
  ai_model: string
  ai_api_key?: string | null
}

/**
 * 两段系统提示词的当前值 + 内置默认。
 * 这两段是**规则文本不是密钥**，可回显；内置默认由后端实时下发（不要在前端硬编码）。
 */
export interface AiPromptRead {
  base_prompt: string
  system_rules: string
  /** 是否已自定义（false = 两段都用内置默认） */
  has_custom: boolean
  default_base_prompt: string
  default_system_rules: string
}

/** 保存载荷；留空 = 该段回落内置默认 */
export interface AiPromptUpdate {
  base_prompt: string
  system_rules: string
  note?: string | null
}

/** 版本历史列表项（不含全文） */
export interface AiPromptVersionSummary {
  id: number
  action: 'save' | 'reset_default' | 'rollback'
  actor_name: string
  base_len: number
  rules_len: number
  base_preview: string
  rules_preview: string
  replaced_from_id: number | null
  note: string | null
  created_at: string
}

/** 单个版本详情（含两段全文，回滚前确认用） */
export interface AiPromptVersionDetail {
  id: number
  action: 'save' | 'reset_default' | 'rollback'
  actor_name: string
  base_prompt: string
  system_rules: string
  previous_base_prompt: string | null
  previous_system_rules: string | null
  replaced_from_id: number | null
  note: string | null
  created_at: string
}

/** 用页面上的两段文本试跑（不落库） */
export interface AiPromptPreviewPayload {
  template_id: number
  placeholder: string
  period: string
  base_prompt: string
  system_rules: string
}

/** 试跑结果（与模板页试跑同构） */
export interface AiPromptPreviewResult {
  text: string
  unverified_numbers: string[]
  sample_leak: string[]
  degraded: boolean
  retried: boolean
  model: string
  metric_count: number
}

export const systemApi = {
  /** baseURL 已是 /api，这里只写相对路径 */
  async settings() {
    const { data } = await http.get<SystemSettings>('/system/settings')
    return data
  },

  /** 保存邮件集成（需管理员），返回保存后的最新概览 */
  async updateMail(payload: MailSettingsUpdate) {
    const { data } = await http.put<SystemSettings>('/system/settings/mail', payload)
    return data
  },

  /** 保存 AI 集成（需管理员），返回保存后的最新概览 */
  async updateAi(payload: AiSettingsUpdate) {
    const { data } = await http.put<SystemSettings>('/system/settings/ai', payload)
    return data
  },

  /** 读取两段系统提示词（当前值 + 内置默认） */
  async aiPrompt() {
    const { data } = await http.get<AiPromptRead>('/system/settings/ai-prompt')
    return data
  },

  /** 保存两段系统提示词（需管理员）：立即生效，无需重启 */
  async saveAiPrompt(payload: AiPromptUpdate) {
    const { data } = await http.put<AiPromptRead>('/system/settings/ai-prompt', payload)
    return data
  },

  /** 恢复内置默认（需管理员）：清空覆盖值，历史保留 */
  async resetAiPrompt() {
    const { data } = await http.post<AiPromptRead>('/system/settings/ai-prompt/reset')
    return data
  },

  /** 版本历史（最新在前，列表不含全文） */
  async aiPromptVersions(limit = 50) {
    const { data } = await http.get<AiPromptVersionSummary[]>(
      '/system/settings/ai-prompt/versions',
      { params: { limit } },
    )
    return data
  },

  /** 单个版本详情（含两段全文） */
  async aiPromptVersion(id: number) {
    const { data } = await http.get<AiPromptVersionDetail>(
      `/system/settings/ai-prompt/versions/${id}`,
    )
    return data
  },

  /** 回滚到指定版本（需管理员）：另记一条历史，不改写已有记录 */
  async rollbackAiPrompt(versionId: number) {
    const { data } = await http.post<AiPromptRead>('/system/settings/ai-prompt/rollback', {
      version_id: versionId,
    })
    return data
  },

  /** 用页面上的两段文本试跑一次（需管理员，不落库） */
  async previewAiPrompt(payload: AiPromptPreviewPayload) {
    const { data } = await http.post<AiPromptPreviewResult>(
      '/system/settings/ai-prompt/preview',
      payload,
    )
    return data
  },
}