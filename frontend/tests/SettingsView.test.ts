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

  it('publishes the fixed reminder operation menu', async () => {
    const requests: Array<{ path: string; method: string }> = []
    setApiFetcher(
      vi.fn(async (input, init) => {
        const path = String(input)
        const method = init?.method ?? 'GET'
        requests.push({ path, method })
        if (path.endsWith('/admin/ai/profiles')) {
          return new Response(JSON.stringify({ data: [] }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          })
        }
        if (method === 'POST' && path.endsWith('/admin/wecom/menu/publish')) {
          return new Response(JSON.stringify({ data: { applied: true } }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          })
        }
        return new Response(
          JSON.stringify({
            data: {
              timezone: 'Asia/Shanghai',
              retention_days: 90,
              version: '0.7.2',
              wecom: {
                configured: true,
                corp_id_configured: true,
                agent_id_configured: true,
                secret_configured: true,
                callback_token_configured: true,
                aes_key_configured: true,
                api_base_url: 'https://qyapi.weixin.qq.com',
                using_proxy: false,
              },
            },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }) as typeof fetch,
    )

    const wrapper = mount(SettingsView, { global: { plugins: [createPinia()] } })
    await flushPromises()
    const publish = wrapper.findAll('button').find((button) =>
      button.text().includes('发布三系列菜单'),
    )
    expect(publish).toBeTruthy()
    await publish?.trigger('click')
    await flushPromises()

    expect(requests).toContainEqual({
      path: '/api/v1/admin/wecom/menu/publish',
      method: 'POST',
    })
  })
})
