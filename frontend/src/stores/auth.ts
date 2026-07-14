import { defineStore } from 'pinia'
import { api } from '@/lib/api'

interface Tokens {
  access_token: string
  refresh_token: string
  administrator?: { name: string }
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('notify_hub_access_token'),
    initialized: true,
    administrator: null as null | { name: string },
  }),
  getters: { authenticated: (state) => Boolean(state.token) },
  actions: {
    async bootstrap() {
      const status = await api.get<{ initialized: boolean }>('/admin/auth/status')
      this.initialized = status.initialized
    },
    persist(data: Tokens, fallbackName: string) {
      this.token = data.access_token
      this.administrator = data.administrator ?? { name: fallbackName }
      localStorage.setItem('notify_hub_access_token', data.access_token)
      localStorage.setItem('notify_hub_refresh_token', data.refresh_token)
    },
    async login(username: string, password: string) {
      this.persist(await api.post<Tokens>('/admin/auth/login', { username, password }), username)
    },
    async initialize(username: string, password: string) {
      this.persist(
        await api.post<Tokens>('/admin/auth/initialize', { username, password }),
        username,
      )
      this.initialized = true
    },
    async logout() {
      const refreshToken = localStorage.getItem('notify_hub_refresh_token')
      try {
        await api.post('/admin/auth/logout',
          refreshToken ? { refresh_token: refreshToken } : undefined,
        )
      } finally {
        this.token = null
        this.administrator = null
        localStorage.removeItem('notify_hub_access_token')
        localStorage.removeItem('notify_hub_refresh_token')
      }
    },
  },
})
