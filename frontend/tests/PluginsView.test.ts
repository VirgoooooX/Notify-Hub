import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { setApiFetcher } from '@/lib/api'
import PluginsView from '@/views/PluginsView.vue'

type CapturedRequest = { url: string; method: string; body?: Record<string, unknown> }

function response(data: unknown) {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

async function openAndSave(scheduleInheritance: boolean | undefined) {
  const requests: CapturedRequest[] = []
  const schedule = { type: 'interval' as const, seconds: 600 }
  setApiFetcher(
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      requests.push({
        url,
        method,
        body: init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined,
      })
      if (url === '/api/v1/admin/plugins') {
        return response([{
          id: 'fake_monitor',
          name: 'Fake Monitor',
          status: 'disabled',
          enabled: false,
          schedule,
          manifest: { default_schedule: schedule, permissions: {} },
        }])
      }
      if (url === '/api/v1/admin/people' || url === '/api/v1/admin/ai/profiles') {
        return response([])
      }
      if (url === '/api/v1/admin/plugins/fake_monitor/secrets') return response([])
      if (url === '/api/v1/admin/plugins/fake_monitor') {
        return response({
          config: { username: 'demo' },
          schedule,
          schedule_inherits_default: scheduleInheritance,
          manifest: { default_schedule: schedule },
        })
      }
      return response({})
    }) as typeof fetch,
  )

  const wrapper = mount(PluginsView, { global: { plugins: [createPinia()] } })
  await flushPromises()
  const configure = wrapper.findAll('button').find((button) => button.text() === '配置')
  expect(configure).toBeDefined()
  await configure!.trigger('click')
  await flushPromises()
  const save = Array.from(document.body.querySelectorAll('button')).find(
    (button) => button.textContent?.trim() === '保存配置',
  )
  expect(save).toBeDefined()
  save!.click()
  await flushPromises()
  wrapper.unmount()
  return requests.filter((request) => request.method !== 'GET')
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('PluginsView schedule compatibility', () => {
  it('discovers a center Profile added after the plugin page was loaded', async () => {
    let profiles: { id: string; name: string; capability: string; enabled: boolean }[] = []
    const manifest = { permissions: { ai_capabilities: ['classify'] } }
    setApiFetcher(vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/v1/admin/plugins') {
        return response([{ id: 'codex_x_monitor', name: 'Fake Monitor', enabled: false, status: 'disabled', manifest }])
      }
      if (url === '/api/v1/admin/ai/profiles') return response(profiles)
      if (url === '/api/v1/admin/plugins/codex_x_monitor') {
        return response({ config: { decision_mode: 'ai_only' }, manifest })
      }
      if (url.endsWith('/secrets') || url.endsWith('/people')) return response([])
      return response({})
    }) as typeof fetch)
    const wrapper = mount(PluginsView, { global: { plugins: [createPinia()] } })
    await flushPromises()
    profiles = [{ id: 'center_new', name: '中心新增分类', capability: 'classify', enabled: true }]
    await wrapper.findAll('button').find(button => button.text() === '配置')!.trigger('click')
    await flushPromises()
    const options = Array.from(document.body.querySelectorAll('option'))
    expect(options.some(option => option.value === 'center_new' && option.textContent?.includes('中心新增分类'))).toBe(true)
    wrapper.unmount()
  })

  it('does not write scheduling when a new backend receives an ordinary config save', async () => {
    const requests = await openAndSave(true)
    expect(requests).toHaveLength(1)
    expect(requests[0].url).toBe('/api/v1/admin/plugins/fake_monitor/config')
    expect(requests[0].body).not.toHaveProperty('schedule')
  })

  it('uses the legacy config payload when the backend lacks schedule capability', async () => {
    const requests = await openAndSave(undefined)
    expect(requests).toHaveLength(1)
    expect(requests[0].url).toBe('/api/v1/admin/plugins/fake_monitor/config')
    expect(requests[0].body?.schedule).toEqual({ type: 'interval', seconds: 600 })
  })
})
