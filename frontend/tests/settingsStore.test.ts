import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { setApiFetcher } from '@/lib/api'
import { DEFAULT_TIMEZONE } from '@/lib/time'
import { useSettingsStore } from '@/stores/settings'

describe('settings store timezone contract', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('uses the platform timezone returned by the settings API', async () => {
    setApiFetcher(vi.fn(async () => new Response(JSON.stringify({ data: { timezone: 'Europe/Berlin' } }), { status: 200 })))
    const store = useSettingsStore()
    await store.load()
    expect(store.timezone).toBe('Europe/Berlin')
    expect(store.loaded).toBe(true)
  })

  it('settles on a deterministic UTC fallback when loading fails', async () => {
    setApiFetcher(vi.fn(async () => new Response('{}', { status: 503 })))
    const store = useSettingsStore()
    await store.load()
    expect(store.timezone).toBe(DEFAULT_TIMEZONE)
    expect(store.loaded).toBe(true)
    expect(store.loadError).toBe(true)
    expect(store.loadErrorStatus).toBe(503)
  })
})
