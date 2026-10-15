import { describe, expect, it } from 'vitest'

import type { MetricFact } from '@/api/types'
import {
  ACTION_LABEL,
  STATUS_LABEL,
  auditActionLabel,
  auditActionSoftClass,
  countAbnormalFacts,
  isConfirmationExpired,
  reportName,
  sourceLabel,
  statusActions,
  statusHasIcon,
  statusSoftClass,
} from '@/utils/report'

describe('reportName', () => {
  it('拼接模板名与统计周期', () => {
    expect(reportName('运维月报模板', '2026-08')).toBe('运维月报模板 2026-08')
  })
})

describe('状态机（7 态）', () => {
  it('状态标签齐全且无旧状态', () => {
    expect(Object.keys(STATUS_LABEL).sort()).toEqual(
      [
        'confirmed',
        'failed',
        'generating',
        'pending',
        'pending_confirmation',
        'push_failed',
        'pushed',
      ].sort(),
    )
    for (const old of ['draft', 'generated', 'approved', 'rejected', 'pending_review']) {
      expect(STATUS_LABEL).not.toHaveProperty(old)
    }
  })

  it('pending / generating 无可用操作', () => {
    expect(statusActions('pending')).toEqual([])
    expect(statusActions('generating')).toEqual([])
  })

  it('pending_confirmation -> 代确认/ 重发确认邮件 / 下载', () => {
    expect(statusActions('pending_confirmation')).toEqual([
      'confirmByAdmin',
      'resendConfirmation',
      'download',
    ])
  })

  it('confirmed -> 重试推送 + 下载', () => {
    expect(statusActions('confirmed')).toEqual(['retryPush', 'download'])
  })

  it('pushed -> 仅下载', () => {
    expect(statusActions('pushed')).toEqual(['download'])
  })

  it('push_failed -> 重试推送 + 下载', () => {
    expect(statusActions('push_failed')).toEqual(['retryPush', 'download'])
  })

  it('failed -> 重新生成 + 下载', () => {
    expect(statusActions('failed')).toEqual(['regenerate', 'download'])
  })

  it('状态色：待确认=warning、已推送=success、失败=danger', () => {
    expect(statusSoftClass('pending_confirmation')).toBe('warning')
    expect(statusSoftClass('pushed')).toBe('success')
    expect(statusSoftClass('push_failed')).toBe('danger')
    expect(statusSoftClass('failed')).toBe('danger')
    expect(statusSoftClass('pending')).toBe('neutral')
  })

  it('只有 pushed 带成功图标', () => {
    expect(statusHasIcon('pushed')).toBe(true)
    expect(statusHasIcon('confirmed')).toBe(false)
  })

  it('操作标签齐全', () => {
    for (const act of Object.keys(ACTION_LABEL)) {
      expect([
        'download',
        'confirmByAdmin',
        'resendConfirmation',
        'retryPush',
        'regenerate',
      ]).toContain(act)
    }
    expect(ACTION_LABEL.confirmByAdmin).toBe('代确认并发送')
  })
})

describe('sourceLabel', () => {
  it('任务触发给任务名，手动给"手动"', () => {
    expect(sourceLabel({ task_name: '每月月报', source: 'cron' })).toBe('每月月报')
    expect(sourceLabel({ task_name: null, source: 'manual' })).toBe('手动')
  })
})

describe('isConfirmationExpired', () => {
  const base = { status: 'pending_confirmation' as const }

  it('expires_at 在过去 -> 已超期', () => {
    expect(isConfirmationExpired({ ...base, expires_at: '2020-01-01T00:00:00Z' })).toBe(true)
  })

  it('expires_at 在未来 -> 未超期', () => {
    expect(isConfirmationExpired({ ...base, expires_at: '2999-01-01T00:00:00Z' })).toBe(false)
  })

  it('非待确认状态 / 无过期时间 -> 不超期', () => {
    expect(isConfirmationExpired({ status: 'pushed', expires_at: '2020-01-01T00:00:00Z' })).toBe(false)
    expect(isConfirmationExpired({ ...base, expires_at: null })).toBe(false)
  })
})

describe('countAbnormalFacts', () => {
  const mk = (change_pct: number | null): MetricFact =>
    ({ change_pct }) as MetricFact

  it('|变化率| > 10% 记为异常', () => {
    const facts = [mk(15), mk(-20), mk(5), mk(null), mk(10)]
    expect(countAbnormalFacts(facts)).toBe(2)
  })
})

describe('审计动作', () => {
  it('新动作词表有中文标签', () => {
    expect(auditActionLabel('generate_ok')).toBe('生成成功')
    expect(auditActionLabel('confirmation_sent')).toBe('发送确认邮件')
    expect(auditActionLabel('confirm')).toBe('确认人确认')
    expect(auditActionLabel('confirm_by_admin')).toBe('管理员代确认')
    expect(auditActionLabel('reminder_sent')).toBe('发送超时提醒')
  })

  it('未知动作回退为原串', () => {
    expect(auditActionLabel('whatever')).toBe('whatever')
  })

  it('颜色：确认/推送=success，失败=danger，提醒=warning', () => {
    expect(auditActionSoftClass('confirm')).toBe('success')
    expect(auditActionSoftClass('push')).toBe('success')
    expect(auditActionSoftClass('generate_fail')).toBe('danger')
    expect(auditActionSoftClass('reminder_sent')).toBe('warning')
  })
})