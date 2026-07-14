import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'
import { setApiFetcher } from '@/lib/api'
import SettingsView from '@/views/SettingsView.vue'

describe('SettingsView', () => {
  it('shows the configured WeCom proxy URL and mode', async () => {
    setApiFetcher(
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              data: {
                timezone: 'Asia/Shanghai',
                retention_days: 90,
                version: '0.3.0',
                wecom: {
                  configured: true,
                  corp_id_configured: true,
                  agent_id_configured: true,
                  secret_configured: true,
                  callback_token_configured: false,
                  aes_key_configured: false,
                  api_base_url: 'https://proxy.example.com/wecom',
                  using_proxy: true,
                },
              },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          ),
      ) as typeof fetch,
    )

    const wrapper = mount(SettingsView, {
      global: { plugins: [createPinia()] },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('https://proxy.example.com/wecom')
    expect(wrapper.text()).toContain('自定义 HTTPS 代理')
  })
})
