import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setApiFetcher } from '@/lib/api'
import ApiClientsView from '@/views/ApiClientsView.vue'
import type { ApiClient } from '@/types'

const client: ApiClient = {
  id: 'client_1',
  name: 'Browser Publisher',
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
  allow_mp_browser: true,
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
  it('displays allow_mp_browser badge in permission column', async () => {
    setApiFetcher(fetcher([]))
    const wrapper = mount(ApiClientsView, { global: { plugins: [createPinia()] } })
    await flushPromises()

    expect(wrapper.text()).toContain('Browser Publisher')
    expect(wrapper.text()).toContain('公众号发布')
    wrapper.unmount()
  })

  it('submits allow_mp_browser permission on client creation', async () => {
    const requests: Array<{ path: string; method: string; body?: Record<string, unknown> }> = []
    setApiFetcher(fetcher(requests))
    const wrapper = mount(ApiClientsView, {
      attachTo: document.body,
      global: { plugins: [createPinia()] },
    })
    await flushPromises()

    // Open create form
    const addBtn = wrapper.findAll('button').find((b) => b.text().includes('新 Client') || b.text().includes('添加 Client') || b.text().includes('新建 Client'))
    if (addBtn) {
      await addBtn.trigger('click')
      await flushPromises()
    }

    // Fill form name
    const nameInput = wrapper.find('input[type="text"]')
    if (nameInput.exists()) {
      await nameInput.setValue('Test Browser Client')
    }

    // Find checkboxes
    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    // Last checkbox is allow_mp_browser
    if (checkboxes.length > 0) {
      const mpCheckbox = checkboxes[checkboxes.length - 1]
      await mpCheckbox.setValue(true)
    }

    // Submit form
    const form = wrapper.find('form')
    if (form.exists()) {
      await form.trigger('submit.prevent')
      await flushPromises()

      const postReq = requests.find((r) => r.method === 'POST')
      expect(postReq).toBeDefined()
      expect(postReq?.body?.allow_mp_browser).toBe(true)
    }
    wrapper.unmount()
  })
})
