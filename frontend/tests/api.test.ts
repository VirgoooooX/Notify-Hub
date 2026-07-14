import { beforeEach, describe, expect, it, vi } from 'vitest'
import { request, setApiFetcher } from '@/lib/api'

describe('API client', () => {
  beforeEach(() => localStorage.clear())

  it('unwraps the standard data envelope', async () => {
    setApiFetcher(
      vi.fn(
        async () =>
          new Response(JSON.stringify({ data: { ok: true } }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
      ) as typeof fetch,
    )
    await expect(request('/admin/dashboard')).resolves.toEqual({ ok: true })
  })

  it('adds admin bearer auth without exposing it in payload', async () => {
    localStorage.setItem('notify_hub_access_token', 'safe-session')
    let captured: RequestInit | undefined
    setApiFetcher((async (_input: RequestInfo | URL, init?: RequestInit) => {
      captured = init
      return new Response(JSON.stringify({ data: {} }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }) as typeof fetch)
    await request('/admin/settings')
    expect(new Headers(captured?.headers).get('Authorization')).toBe('Bearer safe-session')
  })

  it('rotates the refresh token once and retries the failed request', async () => {
    localStorage.setItem('notify_hub_access_token', 'expired-access')
    localStorage.setItem('notify_hub_refresh_token', 'refresh-1')
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response('{}', { status: 401 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            data: { access_token: 'access-2', refresh_token: 'refresh-2' },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ data: { ok: true } }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    setApiFetcher(fetcher as typeof fetch)
    await expect(request('/admin/dashboard')).resolves.toEqual({ ok: true })
    expect(localStorage.getItem('notify_hub_refresh_token')).toBe('refresh-2')
    expect(fetcher).toHaveBeenCalledTimes(3)
  })

  it('normalizes stable API errors', async () => {
    setApiFetcher(
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({ error: { code: 'conflict', message: '非法状态转换' } }),
            { status: 409, headers: { 'Content-Type': 'application/json' } },
          ),
      ) as typeof fetch,
    )
    await expect(request('/admin/deliveries/d1/retry')).rejects.toEqual(
      expect.objectContaining({ status: 409, code: 'conflict', message: '非法状态转换' }),
    )
  })
})
