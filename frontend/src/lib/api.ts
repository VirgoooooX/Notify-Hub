export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message)
  }
}

type Fetcher = typeof fetch
let fetcher: Fetcher = fetch
let refreshPromise: Promise<string | null> | null = null

export function setApiFetcher(next: Fetcher) {
  fetcher = next
}

function accessToken() {
  return localStorage.getItem('notify_hub_access_token')
}

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = localStorage.getItem('notify_hub_refresh_token')
  if (!refreshToken) return null
  const response = await fetcher('/api/v1/admin/auth/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  })
  if (!response.ok) {
    localStorage.removeItem('notify_hub_access_token')
    localStorage.removeItem('notify_hub_refresh_token')
    return null
  }
  const body = (await response.json()) as {
    data: { access_token: string; refresh_token: string }
  }
  localStorage.setItem('notify_hub_access_token', body.data.access_token)
  localStorage.setItem('notify_hub_refresh_token', body.data.refresh_token)
  return body.data.access_token
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
  allowRefresh = true,
): Promise<T> {
  const headers = new Headers(options.headers)
  if (!headers.has('Content-Type') && options.body && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  const auth = accessToken()
  if (auth) headers.set('Authorization', `Bearer ${auth}`)
  const response = await fetcher(`/api/v1${path}`, { ...options, headers })
  if (response.status === 401 && allowRefresh && !path.startsWith('/admin/auth/')) {
    refreshPromise ??= refreshAccessToken().finally(() => {
      refreshPromise = null
    })
    if (await refreshPromise) return request<T>(path, options, false)
  }
  if (response.status === 204) return undefined as T
  const body = (await response.json().catch(() => ({}))) as {
    data?: T
    error?: { code?: string; message?: string; details?: unknown }
  }
  if (!response.ok)
    throw new ApiError(
      response.status,
      body.error?.code ?? 'request_failed',
      body.error?.message ?? `请求失败 (${response.status})`,
      body.error?.details,
    )
  return (body.data ?? body) as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: 'POST', body: data === undefined ? undefined : JSON.stringify(data) }),
  put: <T>(path: string, data: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(data) }),
  patch: <T>(path: string, data: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(data) }),
  upload: <T>(path: string, data: FormData) =>
    request<T>(path, { method: 'POST', body: data }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

export function query(params: Record<string, string | number | boolean | undefined>) {
  const value = new URLSearchParams()
  Object.entries(params).forEach(([key, item]) => {
    if (item !== undefined && item !== '') value.set(key, String(item))
  })
  return value.size ? `?${value}` : ''
}
