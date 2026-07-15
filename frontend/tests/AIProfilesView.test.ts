import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setApiFetcher } from '@/lib/api'
import { useUiStore } from '@/stores/ui'
import AIProfilesView from '@/views/AIProfilesView.vue'

const profile = {
  id: 'semantic_classifier_fast',
  name: '快速语义分类',
  description: '用于低延迟文本分类',
  capability: 'classify',
  provider_id: 'aip_test',
  model: 'model-allowed',
  temperature: 0,
  max_output_tokens: 160,
  response_format: 'auto',
  timeout_seconds: 20,
  output_language: 'zh-CN',
  reasoning_effort: 'low',
  verbosity: 'concise',
  include_reason: true,
  max_reason_characters: 200,
  system_instructions: '',
  cache_ttl_seconds: 2592000,
  daily_request_limit: 500,
  daily_token_limit: 1000000,
  enabled: true,
  revision: 1,
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

describe('AIProfilesView', () => {
  it('shows the complete policy form and only Provider-authorized models', async () => {
    setApiFetcher(
      vi.fn(async (input) => {
        const path = String(input)
        if (path.endsWith('/admin/ai/profiles')) return json([profile])
        if (path.endsWith('/admin/ai/providers')) {
          return json([
            {
              id: 'aip_test',
              name: 'Test Provider',
              preset: 'custom',
              protocol: 'openai_chat_completions',
              base_url: 'https://example.com/v1',
              enabled: true,
              allow_private_network: false,
              timeout_seconds: 30,
              max_retries: 2,
              verify_tls: true,
              structured_output_mode: 'auto',
              api_key_configured: true,
              created_at: '2026-07-15T00:00:00Z',
              updated_at: '2026-07-15T00:00:00Z',
            },
          ])
        }
        if (path.includes('/admin/ai/invocations')) return json([])
        if (path.endsWith('/admin/ai/providers/aip_test/models')) {
          return json({
            models: [
              { id: 'allowed', provider_id: 'aip_test', model_id: 'model-allowed', available: true, enabled: true },
              { id: 'blocked', provider_id: 'aip_test', model_id: 'model-blocked', available: true, enabled: false },
            ],
          })
        }
        throw new Error(`unexpected request: ${path}`)
      }) as typeof fetch,
    )

    const wrapper = mount(AIProfilesView, { global: { plugins: [createPinia()] } })
    await flushPromises()
    await wrapper.get('button.btn--primary').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('身份与能力')
    expect(wrapper.text()).toContain('模型路由')
    expect(wrapper.text()).toContain('输出策略')
    expect(wrapper.text()).toContain('成本可靠性')
    expect(wrapper.text()).toContain('只会追加，不会替换')
    const modelOptions = wrapper.find('#ai-profile-model').findAll('option').map((item) => item.text())
    expect(modelOptions).toContain('model-allowed')
    expect(modelOptions).not.toContain('model-blocked')

    wrapper.unmount()
  })

  it('confirms deletion and keeps the history warning visible', async () => {
    const requests: Array<{ path: string; method: string }> = []
    setApiFetcher(
      vi.fn(async (input, init) => {
        const path = String(input)
        const method = init?.method ?? 'GET'
        requests.push({ path, method })
        if (method === 'DELETE') return new Response(null, { status: 204 })
        if (path.endsWith('/admin/ai/profiles')) return json([profile])
        if (path.endsWith('/admin/ai/providers')) return json([])
        if (path.includes('/admin/ai/invocations')) return json([])
        throw new Error(`unexpected request: ${path}`)
      }) as typeof fetch,
    )

    const wrapper = mount(AIProfilesView, {
      attachTo: document.body,
      global: { plugins: [createPinia()] },
    })
    await flushPromises()
    await wrapper.get('.profile-table .btn--danger').trigger('click')

    expect(document.body.textContent).toContain('历史调用记录会继续保留')
    expect(document.body.textContent).toContain('请先修改或停用相关插件')
    const confirm = [...document.body.querySelectorAll('button')].find(
      (button) => button.textContent?.trim() === '确认删除',
    ) as HTMLButtonElement
    confirm.click()
    await flushPromises()

    expect(requests).toContainEqual({
      path: '/api/v1/admin/ai/profiles/semantic_classifier_fast',
      method: 'DELETE',
    })

    wrapper.unmount()
  })

  it('keeps the delete dialog open and explains active plugin conflicts', async () => {
    setApiFetcher(
      vi.fn(async (input, init) => {
        const path = String(input)
        if (init?.method === 'DELETE') {
          return new Response(
            JSON.stringify({ error: { code: 'ai_profile_in_use', message: 'in use' } }),
            { status: 409, headers: { 'Content-Type': 'application/json' } },
          )
        }
        if (path.endsWith('/admin/ai/profiles')) return json([profile])
        if (path.endsWith('/admin/ai/providers')) return json([])
        if (path.includes('/admin/ai/invocations')) return json([])
        throw new Error(`unexpected request: ${path}`)
      }) as typeof fetch,
    )

    const pinia = createPinia()
    const wrapper = mount(AIProfilesView, {
      attachTo: document.body,
      global: { plugins: [pinia] },
    })
    await flushPromises()
    await wrapper.get('.profile-table .btn--danger').trigger('click')
    const confirm = [...document.body.querySelectorAll('button')].find(
      (button) => button.textContent?.trim() === '确认删除',
    ) as HTMLButtonElement
    confirm.click()
    await flushPromises()

    expect(useUiStore(pinia).toasts.at(-1)?.message).toContain('请先修改或停用相关插件')
    expect(document.body.textContent).toContain('确认删除')

    wrapper.unmount()
  })

  it('updates an existing policy with PATCH and clears optional budgets', async () => {
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
        if (method === 'PATCH') return json({ ...profile, name: '更新后的分类策略' })
        if (path.endsWith('/admin/ai/profiles')) return json([profile])
        if (path.endsWith('/admin/ai/providers')) {
          return json([
            {
              id: 'aip_test',
              name: 'Test Provider',
              preset: 'custom',
              protocol: 'openai_chat_completions',
              base_url: 'https://example.com/v1',
              enabled: true,
              allow_private_network: false,
              timeout_seconds: 30,
              max_retries: 2,
              verify_tls: true,
              structured_output_mode: 'auto',
              api_key_configured: true,
              created_at: '2026-07-15T00:00:00Z',
              updated_at: '2026-07-15T00:00:00Z',
            },
          ])
        }
        if (path.includes('/admin/ai/invocations')) return json([])
        if (path.endsWith('/admin/ai/providers/aip_test/models')) {
          return json({
            models: [
              { id: 'allowed', provider_id: 'aip_test', model_id: 'model-allowed', available: true, enabled: true },
            ],
          })
        }
        throw new Error(`unexpected request: ${path}`)
      }) as typeof fetch,
    )

    const wrapper = mount(AIProfilesView, { global: { plugins: [createPinia()] } })
    await flushPromises()
    const edit = wrapper.findAll('.profile-table button').find((button) => button.text() === '编辑')
    await edit?.trigger('click')
    await flushPromises()

    expect(wrapper.get<HTMLInputElement>('#profile-id').element.disabled).toBe(true)
    expect(wrapper.get<HTMLSelectElement>('#profile-capability').element.disabled).toBe(true)
    await wrapper.get('#profile-name').setValue('更新后的分类策略')
    await wrapper.get('#profile-request-limit').setValue('')
    await wrapper.get('#profile-token-limit').setValue('')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const patchRequest = requests.find((request) => request.method === 'PATCH')
    expect(patchRequest).toMatchObject({
      path: '/api/v1/admin/ai/profiles/semantic_classifier_fast',
      method: 'PATCH',
      body: {
        name: '更新后的分类策略',
        daily_request_limit: null,
        daily_token_limit: null,
      },
    })
    expect(patchRequest?.body).not.toHaveProperty('id')
    expect(patchRequest?.body).not.toHaveProperty('capability')

    wrapper.unmount()
  })
})
