import { computed, ref } from 'vue'
import { api } from '@/lib/api'
import type { ApplicationProfile } from '@/types'

export function useApplicationProfiles() {
  const profiles = ref<ApplicationProfile[]>([])
  const loading = ref(false)

  async function loadProfiles() {
    loading.value = true
    try {
      const data = await api.get<ApplicationProfile[] | { items: ApplicationProfile[] }>('/admin/profiles')
      profiles.value = Array.isArray(data) ? data : data.items
      return profiles.value
    } finally {
      loading.value = false
    }
  }

  const defaultProfileId = computed(() => {
    return profiles.value.find((profile) => profile.is_default)?.id ?? profiles.value[0]?.id ?? ''
  })

  function profileLabel(profileId?: string | null) {
    if (!profileId) return '未绑定'
    const profile = profiles.value.find((item) => item.id === profileId || item.key === profileId)
    return profile ? `${profile.name} · ${profile.key}` : profileId
  }

  function profileById(profileId?: string | null) {
    return profiles.value.find((item) => item.id === profileId || item.key === profileId)
  }

  return { profiles, loading, defaultProfileId, profileLabel, profileById, loadProfiles }
}
