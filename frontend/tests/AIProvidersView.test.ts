import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setApiFetcher } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import AIProvidersView from '@/views/AIProvidersView.vue'

const connection = {
  base_url: 'http://127.0.0.1:8848', enabled: true, allow_private_network: true,
  timeout_seconds: 600, verify_tls: true, application_key_configured: true,
}
function json(data: unknown) {
  return new Response(JSON.stringify({ data }), { headers: { 'Content-Type': 'application/json' } })
}
afterEach(() => { document.body.innerHTML = ''; vi.restoreAllMocks() })

describe('AI Hub connection', () => {
  it('saves connection settings without model, protocol or credential fields', async () => {
    const requests: Array<{ path: string; method: string; body?: Record<string, unknown> }> = []
    setApiFetcher(vi.fn(async (input, init) => {
      const path = String(input)
      requests.push({ path, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
      return json(connection)
    }) as typeof fetch)
    const wrapper = mount(AIProvidersView, { global: { plugins: [createPinia()] } })
    await flushPromises()
    expect(wrapper.text()).toContain('AI Hub 连接')
    expect(wrapper.text()).not.toContain('新增 Provider')
    await wrapper.get('#hub-url').setValue('https://hub.example.test')
    await wrapper.findAll('form')[0]!.trigger('submit')
    await flushPromises()
    expect(requests.at(-1)).toEqual({ path: '/api/v1/admin/ai/hub', method: 'PUT', body: {
      ...connection, base_url: 'https://hub.example.test', application_key_configured: undefined,
    } })
    expect(requests.at(-1)?.body).not.toHaveProperty('application_key_configured')
    wrapper.unmount()
  })

  it('stores the shared application key separately and clears the password field', async () => {
    const requests: Array<{ path: string; body?: Record<string, unknown> }> = []
    setApiFetcher(vi.fn(async (input, init) => {
      requests.push({ path: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined })
      return json(connection)
    }) as typeof fetch)
    const wrapper = mount(AIProvidersView, { global: { plugins: [createPinia()] } })
    await flushPromises()
    await wrapper.get('#hub-key').setValue('test-application-key')
    await wrapper.findAll('form')[1]!.trigger('submit')
    await flushPromises()
    expect(requests.at(-1)).toEqual({ path: '/api/v1/admin/ai/hub/application-key', body: { value: 'test-application-key' } })
    expect((wrapper.get('#hub-key').element as HTMLInputElement).value).toBe('')
    wrapper.unmount()
  })

  it('exports routing for the central application without local model management', async () => {
    const routing = { name: 'Notify Hub', slug: 'notify-hub', profiles: { stable_classifier: { protocol: 'chat', models: ['legacy-model'] } } }
    setApiFetcher(vi.fn(async input => json(String(input).endsWith('/routing') ? routing : connection)) as typeof fetch)
    const wrapper = mount(AIProvidersView, { global: { plugins: [createPinia()] } })
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '导出原有模型路由')!.trigger('click')
    await flushPromises()
    expect((wrapper.get('#hub-routing').element as HTMLTextAreaElement).value).toContain('stable_classifier')
    expect(wrapper.text()).not.toContain('同步 / 配置模型')
    wrapper.unmount()
  })

  it('reports a connection test failure', async () => {
    setApiFetcher(vi.fn(async (_input, init) => init?.method === 'POST'
      ? new Response(JSON.stringify({ error: { message: 'ai_hub_not_configured' } }), { status: 502 })
      : json(connection)) as typeof fetch)
    const pinia = createPinia()
    const wrapper = mount(AIProvidersView, { global: { plugins: [pinia] } })
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '测试已保存的连接')!.trigger('click')
    await flushPromises()
    expect(useUiStore(pinia).toasts.at(-1)?.message).toContain('ai_hub_not_configured')
    wrapper.unmount()
  })
})
