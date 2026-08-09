import { defineStore } from 'pinia'
import { ApiError, api } from '@/lib/api'
import { DEFAULT_TIMEZONE, resolveTimezone } from '@/lib/time'

interface PlatformSettingsResponse {
  timezone?: unknown
}

export const useSettingsStore = defineStore('settings', {
  state: () => ({
    timezone: DEFAULT_TIMEZONE,
    loaded: false,
    loading: false,
    loadError: false,
    loadErrorStatus: null as number | null,
  }),
  actions: {
    async load() {
      if (this.loading) return
      this.loading = true
      this.loadError = false
      this.loadErrorStatus = null
      try {
        const settings = await api.get<PlatformSettingsResponse>('/admin/settings')
        this.timezone = resolveTimezone(
          typeof settings.timezone === 'string' ? settings.timezone : undefined,
          DEFAULT_TIMEZONE,
        )
        this.loaded = true
      } catch (error) {
        // UTC is an explicit, deterministic compatibility fallback.  It keeps
        // a failed settings request from silently inheriting browser timezone.
        this.timezone = DEFAULT_TIMEZONE
        this.loaded = true
        this.loadError = true
        this.loadErrorStatus = error instanceof ApiError ? error.status : null
      } finally {
        this.loading = false
      }
    },
    setTimezone(timezone: string) {
      this.timezone = resolveTimezone(timezone, DEFAULT_TIMEZONE)
      this.loaded = true
    },
  },
})
