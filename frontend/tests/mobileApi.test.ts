import { beforeEach, describe, expect, it, vi } from 'vitest'
import router from '@/router'
import { captureMobileEntry, mobileRequest } from '@/lib/mobileApi'

describe('WeCom mobile reminder entry', () => {
  beforeEach(() => {
    sessionStorage.clear()
    vi.restoreAllMocks()
  })

  it('registers all three public mobile reminder routes', () => {
    const paths = new Map(router.getRoutes().map((route) => [route.path, route.meta.public]))
    expect(paths.get('/m/reminders/new')).toBe(true)
    expect(paths.get('/m/reminders/active')).toBe(true)
    expect(paths.get('/m/reminders/:id')).toBe(true)
  })

  it('keeps the short-lived entry token in session storage and sends it only as a header', async () => {
    captureMobileEntry('signed-member-entry')
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get('X-Mobile-Token')).toBe('signed-member-entry')
      return new Response(JSON.stringify({ data: { items: [] } }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(mobileRequest('/reminders')).resolves.toEqual({ items: [] })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/mobile/reminders',
      expect.objectContaining({ headers: expect.any(Headers) }),
    )
  })
})

