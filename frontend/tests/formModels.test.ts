import { describe, expect, it, vi } from 'vitest'
import {
  aiProfilePolicyPayload,
  defaultAIProfileForm,
  editAIProfileForm,
} from '@/features/ai/profileForm'
import {
  aiProviderPayload,
  defaultAIProviderForm,
  editAIProviderForm,
} from '@/features/ai/providerForm'
import { defaultReminderForm, reminderCreatePayload } from '@/features/reminders/reminderForm'
import type { AIProfile, AIProvider } from '@/types'

describe('feature form models', () => {
  it('builds AI profile create and edit payloads without UI-only fields', () => {
    const profile = {
      ...defaultAIProfileForm('provider-1'),
      id: 'profile-1',
      created_at: '2026-07-16T00:00:00Z',
      updated_at: '2026-07-16T00:00:00Z',
      revision: 2,
    } as AIProfile
    const form = editAIProfileForm(profile)
    form.daily_request_limit = ''

    expect(aiProfilePolicyPayload(form)).toMatchObject({
      provider_id: 'provider-1',
      daily_request_limit: null,
      daily_token_limit: 1000000,
    })
  })

  it('maps provider records and produces the control-plane payload', () => {
    const provider = {
      ...defaultAIProviderForm(),
      id: 'provider-1',
      protocol: 'openai_chat_completions',
      api_key_configured: true,
      created_at: '2026-07-16T00:00:00Z',
      updated_at: '2026-07-16T00:00:00Z',
    } as AIProvider
    const form = editAIProviderForm(provider)

    expect(aiProviderPayload(form)).not.toHaveProperty('api_key')
    expect(aiProviderPayload(form)).toMatchObject({ preset: 'custom', enabled: true })
  })

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
})
