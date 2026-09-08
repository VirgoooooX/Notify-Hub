import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setApiFetcher } from '@/lib/api'
import ApiClientsView from '@/views/ApiClientsView.vue'
import type { ApiClient } from '@/types'

const client: ApiClient = {
  id: 'client_1',
  name: 'Monitoring Client',
  key_prefix: 'nfy_abc12345',
  status: 'active',
  allowed_event_types: [],
  allowed_recipient_ids: [],
  allow_broadcast: false,
  allow_media: false,
  allow_reminders: false,
  allow_recurring: false,
  allow_cron: false,
  allow_interactive: false,
  rate_limit_per_minute: 60,
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify({ data }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function fetcher(requests: Array<{ path: string; method: string; body?: Record<string, unknown> }>) {
  return vi.fn(async (input: unknown, init?: RequestInit) => {
    const path = new URL(String(input), 'http://test').pathname
    const method = init?.method ?? 'GET'
    requests.push({ path, method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined })
    if (path.endsWith('/admin/api-clients') && method === 'GET') {
      return json([client])
    }
    if (path.endsWith('/admin/api-clients') && method === 'POST') {
      return json({ ...client, api_key: 'nfy_secret_test_key' })
    }
    throw new Error(`unexpected request: ${method} ${path}`)
  }) as typeof fetch
}

afterEach(() => {
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('ApiClientsView', () => {
  it('displays configured API clients', async () => {
    setApiFetcher(fetcher([]))
    const wrapper = mount(ApiClientsView, { global: { plugins: [createPinia()] } })
    await flushPromises()

    expect(wrapper.text()).toContain('Monitoring Client')
    wrapper.unmount()
  })
})
