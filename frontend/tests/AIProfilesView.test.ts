import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setApiFetcher } from '@/lib/api'
import AIProfilesView from '@/views/AIProfilesView.vue'

const profile = { id: 'new_center_profile', name: '中心新增分类', capability: 'classify', enabled: true,
  max_output_tokens: 512, temperature: 0, timeout_seconds: 120, reasoning_effort: 'provider_default',
  cache_ttl_seconds: 3600, output_language: 'auto', verbosity: 'standard' }
const invocation = { id: 'old_invocation', profile_id: 'deleted_profile', use_case: 'old_business_use',
  cache_hit: true, status: 'succeeded', created_at: '2026-10-09T00:00:00Z' }
function json(data: unknown) {
  return new Response(JSON.stringify({ data }), { headers: { 'Content-Type': 'application/json' } })
}
afterEach(() => { vi.restoreAllMocks() })

describe('center Profile catalog and local invocation history', () => {
  it('shows new center Profiles without exposing local creation or editing', async () => {
    const methods: string[] = []
    setApiFetcher(vi.fn(async (input, init) => {
      methods.push(init?.method ?? 'GET')
      const path = String(input)
      return json(path.endsWith('/profiles') ? [profile] : path.endsWith('/hub') ? { base_url: 'https://hub.example.test' } : [invocation])
    }) as typeof fetch)
    const wrapper = mount(AIProfilesView, { global: { plugins: [createPinia()] } })
    await flushPromises()
    expect(wrapper.text()).toContain('中心新增分类')
    expect(wrapper.text()).toContain('old_business_use')
    expect(wrapper.find('form').exists()).toBe(false)
    expect(wrapper.findAll('button').some(button => button.text() === '新增 Profile')).toBe(false)
    expect(wrapper.text()).not.toContain('确认删除')
    expect(wrapper.get('a').attributes('href')).toBe('https://hub.example.test')
    expect(methods.every(method => method === 'GET')).toBe(true)
    wrapper.unmount()
  })

  it('keeps local invocation records visible when the center is unavailable', async () => {
    setApiFetcher(vi.fn(async input => String(input).endsWith('/profiles')
      ? new Response(JSON.stringify({ error: { message: 'unavailable' } }), { status: 502 })
      : json(String(input).endsWith('/hub') ? { base_url: '' } : [invocation])) as typeof fetch)
    const wrapper = mount(AIProfilesView, { global: { plugins: [createPinia()] } })
    await flushPromises()
    expect(wrapper.text()).toContain('无法获取 AI Hub')
    expect(wrapper.text()).toContain('old_business_use')
    expect(wrapper.text()).toContain('deleted_profile')
    wrapper.unmount()
  })

  it('refreshes the center catalog to discover Profiles added after page load', async () => {
    let reads = 0
    setApiFetcher(vi.fn(async input => {
      const path = String(input)
      if (path.endsWith('/profiles')) return json(++reads > 1 ? [profile] : [])
      return json(path.endsWith('/hub') ? { base_url: '' } : [])
    }) as typeof fetch)
    const wrapper = mount(AIProfilesView, { global: { plugins: [createPinia()] } })
    await flushPromises()
    expect(wrapper.text()).not.toContain('中心新增分类')
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('中心新增分类')
    wrapper.unmount()
  })
})
