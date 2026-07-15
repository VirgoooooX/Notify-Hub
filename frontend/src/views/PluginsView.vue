<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '@/lib/api'
import type { AIProfile, Plugin, Person, JsonValue, PluginDetailsResponse, PluginSecret } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import PluginCard from '@/features/plugins/PluginCard.vue'
import PluginConfigDrawer from '@/features/plugins/PluginConfigDrawer.vue'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const items = ref<Plugin[]>([])
const people = ref<Person[]>([])
const aiProfiles = ref<AIProfile[]>([])
const running = ref(new Set<string>())
const target = ref<Plugin>()
const editing = ref<(Plugin & { secrets?: PluginSecret[] }) | null>(null)
const busy = ref(false)

const editForm = reactive({
  username: '',
  twscrape_fetch_limit: 40,
  interval_seconds: 180,
  include_replies: false,
  include_reposts: false,
  decision_mode: 'rules',
  ai_profile: 'semantic_classifier_fast',
  ai_min_confidence: 0.8,
  rule_ai_threshold: 0.8,
  source: 'twscrape',
  feed_url: '',
  cover_image_url: '',
  fallback_cover_url: '',
  recipients: [] as string[],
  secrets: {} as Record<string, string>
})

async function load() {
  try {
    const data = await api.get<Plugin[] | { items: Plugin[] }>('/admin/plugins')
    items.value = Array.isArray(data) ? data : data.items
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '插件加载失败', 'danger')
  }
}

async function loadPeople() {
  try {
    const data = await api.get<Person[] | { items: Person[] }>('/admin/people')
    people.value = Array.isArray(data) ? data : data.items
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '接收人加载失败', 'danger')
  }
}

async function loadAiProfiles() {
  try {
    aiProfiles.value = await api.get<AIProfile[]>('/admin/ai/profiles')
  } catch {
    aiProfiles.value = []
  }
}

async function run(item: Plugin) {
  if (running.value.has(item.id)) return
  running.value.add(item.id)
  try {
    await api.post(`/admin/plugins/${item.id}/run`)
    ui.toast(`${item.name} 已进入运行队列`, 'success')
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '运行失败', 'danger')
  } finally {
    window.setTimeout(() => running.value.delete(item.id), 1800)
  }
}

async function toggle() {
  if (!target.value) return
  busy.value = true
  const verb = target.value.enabled ? 'disable' : 'enable'
  try {
    await api.post(`/admin/plugins/${target.value.id}/${verb}`)
    ui.toast(`插件已${target.value.enabled ? '停用' : '启用'}`, 'success')
    target.value = undefined
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '操作失败', 'danger')
  } finally {
    busy.value = false
  }
}

async function configure(item: Plugin) {
  try {
    const [details, secretsData] = await Promise.all([
      api.get<PluginDetailsResponse>(`/admin/plugins/${item.id}`),
      api.get<PluginSecret[]>(`/admin/plugins/${item.id}/secrets`)
    ])

    editing.value = { ...item, secrets: secretsData }

    const conf = details.config || {}
    editForm.username = (conf.username as string) || ''
    editForm.twscrape_fetch_limit = (conf.twscrape_fetch_limit as number) || 40

    const sched =
      details.schedule && typeof details.schedule === 'object' ? details.schedule : null
    editForm.interval_seconds = sched?.seconds || 180

    editForm.include_replies = false
    editForm.include_reposts = !!conf.include_reposts
    editForm.decision_mode = (conf.decision_mode as string) || 'rules'
    editForm.ai_profile = (conf.ai_profile as string) || 'semantic_classifier_fast'
    editForm.ai_min_confidence = (conf.ai_min_confidence as number) ?? 0.8
    editForm.rule_ai_threshold = (conf.rule_ai_threshold as number) ?? 0.8
    editForm.source = (conf.source as string) || 'twscrape'
    editForm.feed_url = (conf.feed_url as string) || ''
    editForm.cover_image_url = (conf.cover_image_url as string) || ''
    editForm.fallback_cover_url = (conf.fallback_cover_url as string) || ''
    editForm.recipients = (conf.recipients as string[]) || []

    editForm.secrets = {}
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '加载配置失败', 'danger')
  }
}

async function saveConfig() {
  if (!editing.value) return
  busy.value = true
  try {
    const pluginId = editing.value.id
    const configData: Record<string, JsonValue> = {
      username: editForm.username,
      include_replies: editForm.include_replies,
      include_reposts: editForm.include_reposts,
      original_posts_only: true,
      recipients: editForm.recipients
    }

    if (pluginId === 'codex_x_monitor') {
      configData.source = editForm.source
      configData.decision_mode = editForm.decision_mode
      configData.ai_profile = editForm.ai_profile
      configData.ai_min_confidence = editForm.ai_min_confidence
      configData.rule_ai_threshold = editForm.rule_ai_threshold
      if (editForm.source === 'rss') {
        configData.feed_url = editForm.feed_url
      } else if (editForm.source === 'twscrape') {
        configData.twscrape_fetch_limit = editForm.twscrape_fetch_limit
      }
      if (editForm.cover_image_url) {
        configData.cover_image_url = editForm.cover_image_url
      }
    } else if (pluginId === 'fabrizio_hwg_monitor') {
      configData.source = 'twscrape'
      configData.twscrape_fetch_limit = editForm.twscrape_fetch_limit
      if (editForm.fallback_cover_url) {
        configData.fallback_cover_url = editForm.fallback_cover_url
      }
    }

    await api.put(`/admin/plugins/${pluginId}/config`, {
      config: configData,
      schedule: {
        type: 'interval',
        seconds: Number(editForm.interval_seconds)
      }
    })

    for (const [secName, secVal] of Object.entries(editForm.secrets)) {
      if (secVal.trim()) {
        await api.put(`/admin/plugins/${pluginId}/secrets/${secName}`, {
          value: secVal.trim()
        })
      }
    }

    ui.toast('配置已更新', 'success')
    editing.value = null
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '保存配置失败', 'danger')
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  load()
  loadPeople()
  loadAiProfiles()
})
</script>

<template>
  <PageHeader title="插件运行台" description="插件只发现事件；投递、去重与 Secret 始终由核心平台掌控。" />

  <PluginConfigDrawer
    :open="Boolean(editing)"
    :plugin="editing"
    :people="people"
    :ai-profiles="aiProfiles"
    :busy="busy"
    :form-state="editForm"
    @close="editing = null"
    @save="saveConfig"
  />

  <EmptyState v-if="!items.length" />
  
  <section v-else class="entity-grid">
    <PluginCard
      v-for="item in items"
      :key="item.id"
      :item="item"
      :running="running.has(item.id)"
      @run="run"
      @configure="configure"
      @toggle="target = item"
    />
  </section>

  <ConfirmDialog
    :open="Boolean(target)"
    :title="`${target?.enabled ? '停用' : '启用'}插件？`"
    :description="
      target?.enabled
        ? '将停止后续调度；当前已入队运行不会被强制中断。'
        : '插件会按已保存配置恢复持久化调度。'
    "
    :busy="busy"
    @cancel="target = undefined"
    @confirm="toggle"
  />
</template>
