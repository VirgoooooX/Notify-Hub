import { describe, expect, it, vi } from 'vitest'
import { defaultReminderForm, reminderCreatePayload } from '@/features/reminders/reminderForm'

describe('feature form models', () => {
  it('builds reminder scheduling and recipient payloads', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-16T12:00:00Z'))
    const form = defaultReminderForm()
    form.schedule_type = 'interval'
    form.recipients = 'person_a, person_b'
    form.require_ack = true

    expect(reminderCreatePayload(form)).toMatchObject({
      schedule: {
        type: 'interval',
        interval_seconds: 3600,
        start_at: '2026-07-16T12:00:00.000Z',
      },
      recipients: ['person_a', 'person_b'],
      repeat: { interval_seconds: 300, max_attempts: 12 },
    })
    vi.useRealTimers()
  })

  it('keeps datetime-local wall values and sends the selected timezone', () => {
    const form = defaultReminderForm('America/New_York')
    form.at = '2026-11-01T01:30'
    form.recipients = 'person_a'

    expect(reminderCreatePayload(form).schedule).toMatchObject({
      type: 'once',
      at: '2026-11-01T01:30',
      timezone: 'America/New_York',
    })
  })

  it('does not silently convert an invalid timezone to UTC', () => {
    const form = defaultReminderForm()
    form.timezone = 'Mars/Olympus'
    form.at = '2026-08-09T09:30'
    form.recipients = 'person_a'

    expect(() => reminderCreatePayload(form)).toThrow('IANA')
  })
})
