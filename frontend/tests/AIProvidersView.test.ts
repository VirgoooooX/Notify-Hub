import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setApiFetcher } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import AIProvidersView from '@/views/AIProvidersView.vue'

const provider = {
  id: 'aip_test',
  name: 'Test Provider',
  preset: 'custom',
  protocol: 'openai_chat_completions',
  base_url: 'http://192.168.31.100:8317/v1',
  enabled: true,
  allow_private_network: true,
  timeout_seconds: 30,
  max_retries: 2,
  verify_tls: true,
  structured_output_mode: 'auto',
  api_key_configured: true,
  created_at: '2026-07-15T00:00:00Z',
  updated_at: '2026-07-15T00:00:00Z',
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify({ data }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('AIProvidersView', () => {
  it('updates an existing Provider without sending an API key', async () => {
    const requests: Array<{ path: string; method: string; body?: Record<string, unknown> }> = []
    setApiFetcher(
      vi.fn(async (input, init) => {
        const path = String(input)
        const method = init?.method ?? 'GET'
        requests.push({
          path,
          method,
          body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
        })
        if (method === 'PATCH') return json({ ...provider, name: 'LAN Provider' })
        if (path.endsWith('/admin/ai/providers')) return json([provider])
        throw new Error(`unexpected request: ${path}`)
      }) as typeof fetch,
    )

    const wrapper = mount(AIProvidersView, { global: { plugins: [createPinia()] } })
    await flushPromises()
    const edit = wrapper.findAll('.provider-table button').find((button) => button.text() === '编辑')
    await edit?.trigger('click')
    await flushPromises()

    const name = wrapper.get<HTMLInputElement>('form.grid input[required]')
    await name.setValue('LAN Provider')
    await wrapper.get('form.grid').trigger('submit')
    await flushPromises()

    const request = requests.find((item) => item.method === 'PATCH')
    expect(request).toMatchObject({
      path: '/api/v1/admin/ai/providers/aip_test',
      method: 'PATCH',
      body: {
        name: 'LAN Provider',
        base_url: 'http://192.168.31.100:8317/v1',
        allow_private_network: true,
      },
    })
    expect(request?.body).not.toHaveProperty('api_key')
    expect(request?.body).not.toHaveProperty('id')
    wrapper.unmount()
  })

  it('confirms deletion and explains Profile reference conflicts', async () => {
    setApiFetcher(
      vi.fn(async (input, init) => {
        const path = String(input)
        if (init?.method === 'DELETE') {
          return new Response(
            JSON.stringify({ error: { code: 'conflict', message: 'provider in use' } }),
            { status: 409, headers: { 'Content-Type': 'application/json' } },
          )
        }
        if (path.endsWith('/admin/ai/providers')) return json([provider])
        throw new Error(`unexpected request: ${path}`)
      }) as typeof fetch,
    )
    const pinia = createPinia()
    const wrapper = mount(AIProvidersView, {
      attachTo: document.body,
      global: { plugins: [pinia] },
    })
    await flushPromises()
    await wrapper.get('.provider-table .btn--danger').trigger('click')

    expect(document.body.textContent).toContain('历史模型配置和调用记录继续保留')
    const confirm = [...document.body.querySelectorAll('button')].find(
      (button) => button.textContent?.trim() === '确认删除',
    ) as HTMLButtonElement
    confirm.click()
    await flushPromises()

    expect(useUiStore(pinia).toasts.at(-1)?.message).toContain('仍被 AI Profile 引用')
    expect(document.body.textContent).toContain('确认删除')
    wrapper.unmount()
  })
})
