<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '@/lib/api'
import type { AIProfile, Plugin, Person, JsonValue, PluginDetailsResponse, PluginSchedule, PluginScheduleMode, PluginSecret } from '@/types'
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
const initialScheduleSignature = ref('')
const scheduleApiAvailable = ref(true)
const currentDefaultSchedule = ref<PluginSchedule | null>(null)

const editForm = reactive({
  username: '',
  twscrape_fetch_limit: 40,
  schedule_mode: 'default' as PluginScheduleMode,
  schedule_interval_minutes: 3,
  schedule_cron_expression: '*/10 * * * *',
  schedule_timezone: 'Asia/Shanghai',
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

function scheduleSignature() {
  if (editForm.schedule_mode === 'default') return 'default'
  if (editForm.schedule_mode === 'interval') {
    return `interval:${Number(editForm.schedule_interval_minutes)}`
  }
  return `cron:${editForm.schedule_cron_expression.trim()}:${editForm.schedule_timezone.trim()}`
}

function schedulePayload(): PluginSchedule {
  if (editForm.schedule_mode === 'default') {
    if (!currentDefaultSchedule.value) throw new Error('插件没有声明默认调度')
    return currentDefaultSchedule.value
  }
  if (editForm.schedule_mode === 'interval') {
    return {
      type: 'interval',
      seconds: Math.round(Number(editForm.schedule_interval_minutes) * 60)
    }
  }
  return {
    type: 'cron',
    expression: editForm.schedule_cron_expression.trim(),
    timezone: editForm.schedule_timezone.trim()
  }
}

function loadScheduleForm(details: PluginDetailsResponse) {
  const schedule = details.schedule
  const defaultSchedule = details.manifest?.default_schedule
  scheduleApiAvailable.value = typeof details.schedule_inherits_default === 'boolean'
  currentDefaultSchedule.value = defaultSchedule ?? null
  editForm.schedule_mode = details.schedule_inherits_default === true
    ? 'default'
    : schedule?.type ?? 'default'
  const interval = schedule?.type === 'interval'
    ? schedule
    : defaultSchedule?.type === 'interval'
      ? defaultSchedule
      : undefined
  editForm.schedule_interval_minutes = interval ? interval.seconds / 60 : 10
  const cron = schedule?.type === 'cron'
    ? schedule
    : defaultSchedule?.type === 'cron'
      ? defaultSchedule
      : undefined
  editForm.schedule_cron_expression = cron?.expression ?? '*/10 * * * *'
  editForm.schedule_timezone = cron?.timezone ?? 'Asia/Shanghai'
  initialScheduleSignature.value = scheduleSignature()
}

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

    loadScheduleForm(details)

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
    if (
      editForm.schedule_mode === 'interval' &&
      (!Number.isFinite(editForm.schedule_interval_minutes) || editForm.schedule_interval_minutes < 1)
    ) {
      throw new Error('调度间隔不能少于 1 分钟')
    }
    if (
      editForm.schedule_mode === 'cron' &&
      (!editForm.schedule_cron_expression.trim() || !editForm.schedule_timezone.trim())
    ) {
      throw new Error('请填写 Cron 表达式和时区')
    }
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

    const scheduleChanged = scheduleSignature() !== initialScheduleSignature.value
    const configRequest: { config: Record<string, JsonValue>; schedule?: PluginSchedule } = {
      config: configData
    }
    if (!scheduleApiAvailable.value) {
      configRequest.schedule = schedulePayload()
    }
    await api.put(`/admin/plugins/${pluginId}/config`, configRequest)

    if (scheduleApiAvailable.value && scheduleChanged) {
      if (editForm.schedule_mode === 'default') {
        await api.delete(`/admin/plugins/${pluginId}/schedule`)
      } else {
        await api.put(`/admin/plugins/${pluginId}/schedule`, { schedule: schedulePayload() })
      }
    }

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
