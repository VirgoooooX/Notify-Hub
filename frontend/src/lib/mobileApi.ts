import { ApiError } from '@/lib/api'

const TOKEN_KEY = 'notify_hub_mobile_token'

export function captureMobileEntry(value: unknown) {
  if (typeof value === 'string' && value) sessionStorage.setItem(TOKEN_KEY, value)
}

export function hasMobileEntry() {
  return Boolean(sessionStorage.getItem(TOKEN_KEY))
}

export async function mobileRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = sessionStorage.getItem(TOKEN_KEY)
  const headers = new Headers(options.headers)
  if (token) headers.set('X-Mobile-Token', token)
  if (!headers.has('Content-Type') && options.body && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(`/api/v1/mobile${path}`, { ...options, headers })
  const body = (await response.json().catch(() => ({}))) as {
    data?: T
    error?: { code?: string; message?: string; details?: unknown }
  }
  if (!response.ok) {
    throw new ApiError(
      response.status,
      body.error?.code ?? 'request_failed',
      body.error?.message ?? `请求失败 (${response.status})`,
      body.error?.details,
    )
  }
  return (body.data ?? body) as T
}

