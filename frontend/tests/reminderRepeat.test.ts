import { describe, expect, it } from 'vitest'
import { defaultReminderForm, reminderCreatePayload } from '@/features/reminders/reminderForm'

describe('interactive reminder repeat rules', () => {
  it('sends the explicit admin completion action independently of the schedule', () => {
    const form = defaultReminderForm('Asia/Shanghai')
    Object.assign(form, { schedule_type: 'interval', interval_minutes: 5760, completion_action: 'publisher_wechat_login' })
    const payload = reminderCreatePayload(form)
    expect(payload.completion_action).toBe('publisher_wechat_login')
    expect(payload.schedule).toMatchObject({ type: 'interval', interval_seconds: 345600 })
  })
  it('keeps expiry nodes inside one once reminder and preserves the selected wall time', () => {
    const form = defaultReminderForm('Asia/Shanghai')
    Object.assign(form, { require_ack: true, repeat_type: 'before_due', repeat_due_at: '2030-01-31T09:00' })
    const payload = reminderCreatePayload(form)
    expect(payload.schedule).toEqual({ type: 'once', at: '2030-01-31T09:00', timezone: 'Asia/Shanghai' })
    expect(payload.repeat).toMatchObject({ type: 'before_due', due_at: '2030-01-31T09:00', days_before_due: [30, 7, 3, 1] })
    expect(payload.repeat).not.toHaveProperty('interval_seconds')
  })

  it('keeps outer scheduling independent from the cron inside the occurrence', () => {
    const form = defaultReminderForm('Asia/Shanghai')
    Object.assign(form, { require_ack: true, repeat_type: 'cron', at: '2030-01-01T09:00', repeat_cron_expression: '0 9 * * 1,3,5' })
    const payload = reminderCreatePayload(form)
    expect(payload.schedule.type).toBe('once')
    expect(payload.repeat).toMatchObject({ type: 'cron', cron_expression: '0 9 * * 1,3,5' })
    expect(payload.repeat).not.toHaveProperty('interval_seconds')
  })

  it('converts fractional elapsed days to seconds without converting wall time in the browser', () => {
    const form = defaultReminderForm('Europe/Berlin')
    Object.assign(form, { require_ack: true, repeat_type: 'offsets', repeat_offset_days: '0，0.5，3' })
    expect(reminderCreatePayload(form).repeat).toMatchObject({ offsets_seconds: [0, 43200, 259200] })
    form.repeat_offset_days = '0,,3'
    expect(() => reminderCreatePayload(form)).toThrow('有效的时间节点')
  })
})
