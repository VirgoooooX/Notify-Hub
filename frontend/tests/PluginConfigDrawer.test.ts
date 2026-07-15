import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import type { AIProfile, Plugin } from '@/types'
import PluginConfigDrawer from '@/features/plugins/PluginConfigDrawer.vue'

function profile(
  id: string,
  capability: AIProfile['capability'],
  enabled = true,
): AIProfile {
  return {
    id,
    name: id,
    description: '',
    capability,
    provider_id: 'aip_test',
    model: 'test-model',
    temperature: 0,
    max_output_tokens: 160,
    response_format: 'auto',
    timeout_seconds: 30,
    output_language: 'auto',
    reasoning_effort: 'provider_default',
    verbosity: 'standard',
    include_reason: false,
    max_reason_characters: 0,
    system_instructions: '',
    cache_ttl_seconds: 0,
    enabled,
    revision: 1,
    created_at: '2026-07-15T00:00:00Z',
    updated_at: '2026-07-15T00:00:00Z',
  }
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('PluginConfigDrawer', () => {
  it('shows enabled profiles authorized by AI capability', () => {
    const plugin: Plugin = {
      id: 'codex_x_monitor',
      name: 'Codex X Monitor',
      status: 'disabled',
      enabled: false,
      manifest: { permissions: { ai_capabilities: ['classify'] } },
    }
    const formState = {
      username: 'thsottiaux',
      twscrape_fetch_limit: 20,
      schedule_mode: 'default' as const,
      schedule_interval_minutes: 5,
      schedule_cron_expression: '*/10 * * * *',
      schedule_timezone: 'Asia/Shanghai',
      include_replies: false,
      include_reposts: false,
      decision_mode: 'rules_then_ai',
      ai_profile: 'stale_profile',
      ai_min_confidence: 0.8,
      rule_ai_threshold: 0.8,
      source: 'twscrape',
      feed_url: '',
      cover_image_url: '',
      fallback_cover_url: '',
      recipients: [],
      secrets: {},
    }
    const wrapper = mount(PluginConfigDrawer, {
      props: {
        open: true,
        plugin,
        people: [],
        aiProfiles: [
          profile('custom_classifier', 'classify'),
          profile('disabled_classifier', 'classify', false),
          profile('summary_profile', 'summarize'),
        ],
        busy: false,
        formState,
      },
    })

    expect(formState.ai_profile).toBe('custom_classifier')
    expect(document.body.textContent).toContain('custom_classifier')
    expect(document.body.textContent).not.toContain('disabled_classifier')
    expect(document.body.textContent).not.toContain('summary_profile')
    wrapper.unmount()
  })
})
