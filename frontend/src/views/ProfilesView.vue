<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Check, ExternalLink, KeyRound, Layers, Menu as MenuIcon, Save, Send, Trash2 } from 'lucide-vue-next'
import { api } from '@/lib/api'
import type { ApplicationProfile, Person, ProfileCapabilityName } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppCheckbox from '@/components/ui/AppCheckbox.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const profiles = ref<ApplicationProfile[]>([])
const people = ref<Person[]>([])
const selected = ref<ApplicationProfile | null>(null)
const members = ref<Array<{ person_id: string; display_name: string; enabled: boolean; person_active: boolean }>>([])
const showCreate = ref(false)
const showEditor = ref(false)
const showMembers = ref(false)
const busy = ref(false)
const deleting = ref<ApplicationProfile | null>(null)
const testRecipient = ref('')

const createForm = reactive({
  key: '',
  name: '',
  agent_id: '' as string | number,
  wecom_secret: '',
  callback_token: '',
  callback_aes_key: ''
})

const capabilityOptions: Array<{ key: ProfileCapabilityName; label: string }> = [
  { key: 'outbound_enabled', label: '出站投递' },
  { key: 'callback_enabled', label: '回调接收' },
  { key: 'menu_enabled', label: '企业微信菜单' },
  { key: 'conversation_enabled', label: '会话命令' },
  { key: 'interactive_enabled', label: '交互提醒' },
  { key: 'mobile_enabled', label: '移动端查询' },
  { key: 'broadcast_enabled', label: '@all 广播' }
]

const editForm = reactive({
  name: '',
  enabled: true,
  capabilities: {} as Record<ProfileCapabilityName, boolean>,
  agent_id: '' as string | number,
  wecom_enabled: true,
  callback_enabled: true,
  wecom_secret: '',
  callback_token: '',
  callback_aes_key: ''
})

const memberState = computed(() => {
  const map = new Map(members.value.map((member) => [member.person_id, member.enabled]))
  return people.value.map((person) => ({
    person,
    enabled: map.get(person.id) ?? false,
    existing: map.has(person.id)
  }))
})

function profileLabel(profileId?: string | null) {
  const profile = profiles.value.find((item) => item.id === profileId || item.key === profileId)
  return profile ? `${profile.name} · ${profile.key}` : profileId || '未绑定'
}

function wecomReady(profile: ApplicationProfile) {
  return Boolean(profile.wecom?.agent_id_configured && profile.wecom.secret_configured)
}

async function load() {
  try {
    const [profileData, peopleData] = await Promise.all([
      api.get<{ items: ApplicationProfile[] }>('/admin/profiles'),
      api.get<Person[]>('/admin/people')
    ])
    profiles.value = profileData.items
    people.value = Array.isArray(peopleData) ? peopleData : []
    if (selected.value) {
      selected.value = profiles.value.find((item) => item.id === selected.value?.id) ?? null
    }
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Profile 加载失败', 'danger')
  }
}

function resetCreate() {
  Object.assign(createForm, {
    key: '',
    name: '',
    agent_id: '',
    wecom_secret: '',
    callback_token: '',
    callback_aes_key: ''
  })
}

async function createProfile() {
  busy.value = true
  try {
    await api.post('/admin/profiles', {
      key: createForm.key.trim(),
      name: createForm.name.trim(),
      agent_id: createForm.agent_id ? Number(createForm.agent_id) : undefined,
      wecom_secret: createForm.wecom_secret || undefined,
      callback_token: createForm.callback_token || undefined,
      callback_aes_key: createForm.callback_aes_key || undefined
    })
    showCreate.value = false
    resetCreate()
    ui.toast('应用 Profile 已创建', 'success')
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Profile 创建失败', 'danger')
  } finally {
    busy.value = false
  }
}

function openEditor(profile: ApplicationProfile) {
  selected.value = profile
  Object.assign(editForm, {
    name: profile.name,
    enabled: profile.enabled,
    capabilities: { ...profile.capabilities },
    agent_id: profile.wecom?.agent_id ?? '',
    wecom_enabled: profile.wecom?.enabled ?? true,
    callback_enabled: profile.wecom?.callback_enabled ?? profile.capabilities.callback_enabled,
    wecom_secret: '',
    callback_token: '',
    callback_aes_key: ''
  })
  showEditor.value = true
}

async function saveProfile() {
  if (!selected.value) return
  busy.value = true
  try {
    await api.patch(`/admin/profiles/${selected.value.id}`, {
      name: editForm.name.trim(),
      enabled: editForm.enabled,
      capabilities: editForm.capabilities
    })
    if (editForm.agent_id || selected.value.wecom?.agent_id_configured || editForm.wecom_secret || editForm.callback_token || editForm.callback_aes_key) {
      await api.patch(`/admin/profiles/${selected.value.id}/wecom`, {
        agent_id: editForm.agent_id ? Number(editForm.agent_id) : undefined,
        enabled: editForm.wecom_enabled,
        callback_enabled: editForm.callback_enabled,
        wecom_secret: editForm.wecom_secret || undefined,
        callback_token: editForm.callback_token || undefined,
        callback_aes_key: editForm.callback_aes_key || undefined
      })
    }
    ui.toast('Profile 配置已保存', 'success')
    showEditor.value = false
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Profile 保存失败', 'danger')
  } finally {
    busy.value = false
  }
}

async function toggle(profile: ApplicationProfile) {
  busy.value = true
  try {
    await api.patch(`/admin/profiles/${profile.id}`, { enabled: !profile.enabled })
    ui.toast(`Profile 已${profile.enabled ? '停用' : '启用'}`, 'success')
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '状态更新失败', 'danger')
  } finally {
    busy.value = false
  }
}

async function removeProfile() {
  if (!deleting.value) return
  busy.value = true
  try {
    await api.delete(`/admin/profiles/${deleting.value.id}`)
    ui.toast('Profile 已删除', 'success')
    deleting.value = null
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Profile 删除失败；有依赖数据时请先停用', 'danger')
  } finally {
    busy.value = false
  }
}

async function openMembers(profile: ApplicationProfile) {
  selected.value = profile
  try {
    const data = await api.get<{ items: typeof members.value }>(`/admin/profiles/${profile.id}/members`)
    members.value = data.items
    showMembers.value = true
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '成员加载失败', 'danger')
  }
}

async function saveMember(person: Person, enabled: boolean, existing: boolean) {
  if (!selected.value) return
  try {
    if (enabled) {
      await api.put(`/admin/profiles/${selected.value.id}/members/${person.id}`, { enabled: true })
    } else if (existing) {
      await api.delete(`/admin/profiles/${selected.value.id}/members/${person.id}`)
    }
    const data = await api.get<{ items: typeof members.value }>(`/admin/profiles/${selected.value.id}/members`)
    members.value = data.items
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '成员绑定失败', 'danger')
  }
}

async function publishMenu(profile: ApplicationProfile) {
  busy.value = true
  try {
    await api.post(`/admin/profiles/${profile.id}/wecom/menu/publish`)
    ui.toast(`${profileLabel(profile.id)} 的菜单已发布`, 'success')
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '菜单发布失败', 'danger')
  } finally {
    busy.value = false
  }
}

async function sendTest() {
  if (!selected.value || !testRecipient.value.trim()) return
  busy.value = true
  try {
    await api.post(`/admin/profiles/${selected.value.id}/wecom/test`, {
      recipient_id: testRecipient.value.trim()
    })
    ui.toast('Profile 测试消息已进入投递队列', 'success')
    testRecipient.value = ''
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '测试消息发送失败', 'danger')
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <PageHeader title="应用 Profiles" description="在同一套核心、数据库和 Worker 中隔离多套企业微信应用运行时。">
    <AppButton variant="primary" @click="showCreate = !showCreate">
      {{ showCreate ? '取消创建' : '新建 Profile' }}
    </AppButton>
  </PageHeader>

  <AppAlert variant="info" class="page-note">
    Profile 是通知、回调、提醒、会话和媒体引用的隔离边界；Secret 只写入加密 SecretStore，列表不会返回明文。
  </AppAlert>

  <AppCard v-if="showCreate" padding="md" class="form-card">
    <template #header>
      <h3 class="panel-title">
        创建应用 Profile
      </h3>
    </template>
    <form class="form-grid" @submit.prevent="createProfile">
      <div class="field">
        <label>稳定 Key</label><AppInput v-model="createForm.key" placeholder="例如: ops-prod" required />
      </div>
      <div class="field">
        <label>显示名称</label><AppInput v-model="createForm.name" placeholder="运营通知" required />
      </div>
      <div class="field">
        <label>企业微信 Agent ID</label><AppInput v-model="createForm.agent_id" type="number" min="1" />
      </div>
      <div class="field">
        <label>应用 Secret（只写入）</label><AppInput v-model="createForm.wecom_secret" type="password" autocomplete="new-password" />
      </div>
      <div class="field">
        <label>回调 Token（只写入）</label><AppInput v-model="createForm.callback_token" type="password" autocomplete="new-password" />
      </div>
      <div class="field">
        <label>回调 AES Key（只写入）</label><AppInput v-model="createForm.callback_aes_key" type="password" autocomplete="new-password" />
      </div>
      <div class="form-actions">
        <AppButton type="submit" variant="primary" :loading="busy">
          <Save :size="14" />保存 Profile
        </AppButton>
      </div>
    </form>
  </AppCard>

  <EmptyState v-if="!profiles.length" title="暂无应用 Profile" description="创建第一个 Profile 后，所有新链路都会带上明确的 Profile 边界。" />

  <section v-else class="profile-grid">
    <AppCard v-for="profile in profiles" :key="profile.id" padding="md" class="profile-card">
      <template #header>
        <div class="card-header">
          <div class="profile-heading">
            <Layers :size="18" class="profile-icon" />
            <div><h3>{{ profile.name }}</h3><span class="mono muted">{{ profile.key }} · {{ profile.id }}</span></div>
          </div>
          <div class="header-badges">
            <StatusBadge :status="profile.enabled ? 'active' : 'disabled'" /><span v-if="profile.is_default" class="default-tag">系统默认</span>
          </div>
        </div>
      </template>

      <div class="profile-summary">
        <div class="summary-row">
          <span>企业微信</span><strong :class="wecomReady(profile) ? 'ready' : 'warning'">{{ wecomReady(profile) ? '已就绪' : '待配置' }}</strong>
        </div>
        <div class="summary-row">
          <span>Agent ID</span><strong class="mono">{{ profile.wecom?.agent_id ?? '—' }}</strong>
        </div>
        <div class="summary-row">
          <span>回调 / 菜单</span><strong>{{ profile.wecom?.callback_enabled ? '回调开启' : '回调关闭' }} · {{ profile.capabilities.menu_enabled ? '可发布' : '已禁用' }}</strong>
        </div>
      </div>
      <div class="capability-list">
        <span v-for="capability in capabilityOptions" :key="capability.key" class="capability-chip" :class="{ enabled: profile.capabilities[capability.key] }">
          <Check v-if="profile.capabilities[capability.key]" :size="11" />{{ capability.label }}
        </span>
      </div>
      <template #footer>
        <div class="card-actions">
          <AppButton size="sm" @click="openEditor(profile)">
            <KeyRound :size="13" />配置
          </AppButton>
          <AppButton size="sm" @click="openMembers(profile)">
            <ExternalLink :size="13" />成员
          </AppButton>
          <AppButton size="sm" :disabled="profile.is_default || busy" @click="toggle(profile)">
            {{ profile.enabled ? '停用' : '启用' }}
          </AppButton>
          <AppButton v-if="profile.capabilities.menu_enabled" size="sm" :disabled="busy" @click="publishMenu(profile)">
            <MenuIcon :size="13" />发布菜单
          </AppButton>
          <AppButton v-if="!profile.is_default" size="sm" variant="danger" :disabled="busy" @click="deleting = profile">
            <Trash2 :size="13" />删除
          </AppButton>
        </div>
      </template>
    </AppCard>
  </section>

  <AppDrawer :model-value="showEditor" title="Profile 配置" size="md" @update:model-value="showEditor = $event" @close="showEditor = false">
    <form class="drawer-form" @submit.prevent="saveProfile">
      <div class="field">
        <label>显示名称</label><AppInput v-model="editForm.name" required />
      </div>
      <div class="inline-fields">
        <AppCheckbox v-model="editForm.enabled">
          Profile 启用
        </AppCheckbox>
      </div>
      <div class="section-title">
        运行能力
      </div>
      <div class="capability-editor">
        <AppCheckbox v-for="capability in capabilityOptions" :key="capability.key" v-model="editForm.capabilities[capability.key]">
          {{ capability.label }}
        </AppCheckbox>
      </div>
      <div class="section-title">
        企业微信运行时
      </div>
      <div class="field">
        <label>Agent ID</label><AppInput v-model="editForm.agent_id" type="number" min="1" />
      </div>
      <div class="inline-fields">
        <AppCheckbox v-model="editForm.wecom_enabled">
          应用配置启用
        </AppCheckbox><AppCheckbox v-model="editForm.callback_enabled">
          允许回调
        </AppCheckbox>
      </div>
      <p class="field-help">
        留空 Secret 表示保持现有值；Secret 从不回显。
      </p>
      <div class="field">
        <label>应用 Secret</label><AppInput v-model="editForm.wecom_secret" type="password" autocomplete="new-password" />
      </div>
      <div class="field">
        <label>回调 Token</label><AppInput v-model="editForm.callback_token" type="password" autocomplete="new-password" />
      </div>
      <div class="field">
        <label>回调 AES Key</label><AppInput v-model="editForm.callback_aes_key" type="password" autocomplete="new-password" />
      </div>
      <div class="section-title">
        Profile 链路测试
      </div>
      <div class="test-row">
        <AppInput v-model="testRecipient" placeholder="企业微信 UserID" /><AppButton type="button" :loading="busy" @click="sendTest">
          <Send :size="13" />入队测试
        </AppButton>
      </div>
    </form>
    <template #footer>
      <AppButton @click="showEditor = false">
        取消
      </AppButton><AppButton variant="primary" :loading="busy" @click="saveProfile">
        保存配置
      </AppButton>
    </template>
  </AppDrawer>

  <AppDrawer :model-value="showMembers" title="Profile 成员" size="sm" @update:model-value="showMembers = $event" @close="showMembers = false">
    <p class="drawer-description">
      {{ selected ? profileLabel(selected.id) : '' }} 的广播受众和提醒接收人由以下 membership 决定。
    </p>
    <div class="members-list">
      <div v-for="entry in memberState" :key="entry.person.id" class="member-row">
        <div><strong>{{ entry.person.name }}</strong><span class="mono muted">{{ entry.person.id }}</span></div>
        <AppCheckbox :model-value="entry.enabled" :disabled="!entry.person.enabled" @update:model-value="saveMember(entry.person, $event, entry.existing)">
          启用
        </AppCheckbox>
      </div>
      <EmptyState v-if="!memberState.length" title="暂无接收人" description="请先在接收人页面创建 Person。" />
    </div>
  </AppDrawer>

  <ConfirmDialog :open="Boolean(deleting)" title="删除应用 Profile？" :description="`将删除“${deleting?.name ?? ''}”。有事件、提醒或投递依赖时服务端会拒绝删除，可先停用。`" confirm-text="确认删除" danger :busy="busy" @cancel="deleting = null" @confirm="removeProfile" />
</template>

<style scoped>
.page-note { margin-bottom: var(--space-4); }
.form-card { margin-bottom: var(--space-4); }
.panel-title { margin: 0; font-size: var(--text-md); }
.form-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-4); }
.field { display: flex; flex-direction: column; gap: var(--space-2); }
.field label { color: var(--text-secondary); font-size: var(--text-xs); font-weight: 600; }
.field-help, .drawer-description { color: var(--text-tertiary); font-size: var(--text-xs); line-height: 1.6; }
.form-actions { grid-column: 1 / -1; }
.profile-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-4); }
.profile-card { min-width: 0; }
.card-header, .profile-heading, .header-badges, .summary-row, .card-actions, .inline-fields, .test-row, .member-row { display: flex; align-items: center; }
.card-header, .summary-row, .member-row { justify-content: space-between; gap: var(--space-3); }
.profile-heading { gap: var(--space-3); min-width: 0; }
.profile-icon { color: var(--action-primary); flex: 0 0 auto; }
.profile-heading h3 { margin: 0; font-size: var(--text-md); }
.profile-heading .mono { display: block; margin-top: 3px; font-size: 10px; overflow: hidden; text-overflow: ellipsis; }
.header-badges { flex-wrap: wrap; justify-content: flex-end; gap: var(--space-2); }
.default-tag { border-radius: var(--radius-pill); background: rgba(31, 107, 79, .1); color: #1f6b4f; font: 700 10px var(--font-mono); padding: 4px 7px; }
.profile-summary { display: grid; gap: var(--space-2); padding: var(--space-3) 0; border-bottom: 1px solid var(--border-subtle); }
.summary-row { font-size: var(--text-sm); }
.summary-row span { color: var(--text-secondary); }
.summary-row strong { font-weight: 600; }
.ready { color: var(--status-success); }.warning { color: var(--status-warning); }
.capability-list, .capability-editor { display: flex; flex-wrap: wrap; gap: 6px; margin-top: var(--space-3); }
.capability-chip { display: inline-flex; align-items: center; gap: 3px; padding: 4px 7px; border: 1px solid var(--border-subtle); border-radius: var(--radius-pill); color: var(--text-tertiary); font-size: 10px; }
.capability-chip.enabled { border-color: rgba(31, 107, 79, .2); background: rgba(31, 107, 79, .07); color: #1f6b4f; }
.card-actions { flex-wrap: wrap; gap: 6px; }
.drawer-form { display: flex; flex-direction: column; gap: var(--space-4); }
.inline-fields { flex-wrap: wrap; gap: var(--space-4); }
.section-title { padding-top: var(--space-2); border-top: 1px solid var(--border-subtle); color: var(--text-primary); font-size: var(--text-sm); font-weight: 700; }
.capability-editor { margin-top: calc(var(--space-4) * -1); }
.test-row { align-items: stretch; gap: var(--space-2); }.test-row > :first-child { flex: 1; }
.members-list { display: grid; gap: var(--space-2); }
.member-row { padding: var(--space-3) 0; border-bottom: 1px solid var(--border-subtle); }
.member-row > div { display: flex; flex-direction: column; gap: 3px; }.member-row .mono { font-size: 10px; }
@media (max-width: 900px) { .profile-grid, .form-grid { grid-template-columns: 1fr; } }
</style>
