<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '@/lib/api'
import type { Person } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppCheckbox from '@/components/ui/AppCheckbox.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const items = ref<Person[]>([])
const show = ref(false)
const busy = ref(false)

const form = reactive({
  name: '',
  user_id: '',
  is_default: false
})

// For inline binding forms
const bindForms = reactive<Record<string, string>>({})

// Custom ConfirmDialog state
const confirmOpen = ref(false)
const confirmTitle = ref('')
const confirmDesc = ref('')
const confirmDanger = ref(true)
let confirmCallback: (() => Promise<void>) | null = null

function triggerConfirm(title: string, desc: string, callback: () => Promise<void>, isDanger = true) {
  confirmTitle.value = title
  confirmDesc.value = desc
  confirmCallback = callback
  confirmDanger.value = isDanger
  confirmOpen.value = true
}

async function handleConfirm() {
  if (confirmCallback) {
    busy.value = true
    try {
      await confirmCallback()
    } finally {
      busy.value = false
      confirmOpen.value = false
      confirmCallback = null
    }
  }
}

async function load() {
  try {
    const data = await api.get<Person[] | { items: Person[] }>('/admin/people')
    items.value = Array.isArray(data) ? data : data.items
    items.value.forEach(p => {
      if (!(p.id in bindForms)) {
        bindForms[p.id] = ''
      }
    })
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '接收人加载失败', 'danger')
  }
}

async function create() {
  busy.value = true
  try {
    const person = await api.post<Person>('/admin/people', {
      name: form.name,
      is_default: form.is_default
    })
    if (form.user_id.trim()) {
      await api.post(`/admin/people/${person.id}/wecom-identities`, {
        user_id: form.user_id.trim()
      })
    }
    show.value = false
    Object.assign(form, { name: '', user_id: '', is_default: false })
    ui.toast('接收人已创建', 'success')
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '创建失败', 'danger')
  } finally {
    busy.value = false
  }
}

function removePerson(id: string) {
  triggerConfirm(
    '确认删除该接收人吗？',
    '相关企业微信关联也将一并解绑，此操作不可撤销。',
    async () => {
      await api.delete(`/admin/people/${id}`)
      ui.toast('接收人已删除', 'success')
      await load()
    }
  )
}

function removeIdentity(personId: string, identityId: string) {
  triggerConfirm(
    '确定解绑此企业微信身份吗？',
    '该接收人将无法通过该 UserID 接收通知。',
    async () => {
      await api.delete(`/admin/people/${personId}/wecom-identities/${identityId}`)
      ui.toast('已解绑企业微信身份', 'success')
      await load()
    }
  )
}

async function bindIdentity(personId: string) {
  const userId = bindForms[personId]?.trim()
  if (!userId) return
  try {
    await api.post(`/admin/people/${personId}/wecom-identities`, {
      user_id: userId
    })
    bindForms[personId] = ''
    ui.toast('绑定成功', 'success')
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '绑定失败', 'danger')
  }
}

async function toggleDefault(person: Person) {
  try {
    await api.patch(`/admin/people/${person.id}`, {
      is_default: !person.is_default
    })
    ui.toast('默认接收人设置已更新', 'success')
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '更新失败', 'danger')
  }
}

onMounted(load)
</script>

<template>
  <PageHeader title="接收人" description="内部 Person 与企业微信 UserID 分离管理；空接收人永远不会隐式广播。">
    <AppButton variant="primary" @click="show = !show">
      {{ show ? '取消创建' : '添加接收人' }}
    </AppButton>
  </PageHeader>

  <AppCard v-if="show" padding="md" class="create-card">
    <template #header>
      <h3 class="panel-title">
        新接收人
      </h3>
    </template>
    <form class="create-form" @submit.prevent="create">
      <div class="field">
        <label>显示名称</label>
        <AppInput v-model="form.name" required placeholder="例如：夏振东" />
      </div>
      <div class="field">
        <label>企业微信 UserID (选填)</label>
        <AppInput v-model="form.user_id" placeholder="例如：XiaZhendong" />
      </div>
      <div class="form-checkbox-row">
        <AppCheckbox v-model="form.is_default">
          设为默认接收人
        </AppCheckbox>
      </div>
      <div class="form-actions-row">
        <AppButton type="submit" variant="primary" :loading="busy">
          保存接收人
        </AppButton>
      </div>
    </form>
  </AppCard>

  <AppAlert variant="warning" class="warning-alert">
    广播到 @all 是独立高危权限，不会由“默认接收人”设置自动启用。
  </AppAlert>

  <EmptyState v-if="!items.length" />

  <section v-else class="entity-grid">
    <AppCard v-for="person in items" :key="person.id" padding="md" class="person-card">
      <template #header>
        <div class="card-header-wrap">
          <div>
            <h3 class="person-name">
              {{ person.name }}
            </h3>
            <span class="mono muted item-id">{{ person.id }}</span>
          </div>
          <div class="header-actions">
            <StatusBadge :status="person.enabled === false ? 'disabled' : 'active'" />
            <AppButton variant="danger" size="sm" @click="removePerson(person.id)">
              删除
            </AppButton>
          </div>
        </div>
      </template>

      <div class="person-body">
        <div class="default-badge-trigger" @click="toggleDefault(person)">
          <span v-if="person.is_default" class="default-badge">⭐ 默认通知接收人</span>
          <span v-else class="default-badge-inactive">普通接收人 (点击设为默认)</span>
        </div>

        <div class="identities-section">
          <div class="identities-title">
            企业微信身份
          </div>
          <div class="identities-list">
            <div
              v-for="identity in person.wecom_identities ?? []"
              :key="identity.id"
              class="identity-row"
            >
              <span class="mono identity-text">WeCom: {{ identity.user_id }}</span>
              <div class="identity-actions">
                <span class="identity-badge" :class="{ verified: identity.verified }">
                  {{ identity.verified ? '已验证' : '手工关联' }}
                </span>
                <button
                  type="button"
                  class="unbind-btn"
                  title="解绑身份"
                  @click="removeIdentity(person.id, identity.id)"
                >
                  ×
                </button>
              </div>
            </div>
            
            <div v-if="!person.wecom_identities?.length" class="no-identities">
              尚未关联企业微信身份
            </div>
          </div>
        </div>
      </div>

      <template v-if="!person.wecom_identities?.length" #footer>
        <form class="bind-form" @submit.prevent="bindIdentity(person.id)">
          <AppInput
            v-model="bindForms[person.id]"
            placeholder="绑定企业微信 UserID"
            required
            class="bind-input"
          />
          <AppButton type="submit" variant="primary" size="sm">
            绑定
          </AppButton>
        </form>
      </template>
    </AppCard>
  </section>

  <ConfirmDialog
    :open="confirmOpen"
    :title="confirmTitle"
    :description="confirmDesc"
    :danger="confirmDanger"
    :busy="busy"
    @cancel="confirmOpen = false"
    @confirm="handleConfirm"
  />
</template>

<style scoped>
.create-card {
  margin-bottom: var(--space-4);
}

.panel-title {
  font-size: var(--text-md);
  font-weight: 700;
  margin: 0;
}

.create-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.field label {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  font-weight: 600;
}

.form-checkbox-row {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  height: 38px;
}

.form-actions-row {
  grid-column: 1 / -1;
}

.warning-alert {
  margin-bottom: var(--space-4);
}

.person-card {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.card-header-wrap {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
}

.card-header-wrap > div:first-child {
  min-width: 0;
  flex: 1;
}

.person-name {
  font-size: var(--text-md);
  font-weight: 700;
  margin: 0;
}

.item-id {
  font-size: 11px;
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

.default-badge-trigger {
  margin: var(--space-2) 0;
  font-size: var(--text-sm);
  cursor: pointer;
  display: inline-block;
}

.default-badge {
  font-weight: 600;
  color: var(--color-blue-500);
}

.default-badge-inactive {
  text-decoration: underline;
  text-decoration-color: var(--border-subtle);
  color: var(--text-secondary);
}

.default-badge-inactive:hover {
  color: var(--text-primary);
  text-decoration-color: var(--text-primary);
}

.identities-section {
  margin-top: var(--space-4);
  border-top: 1px solid var(--border-subtle);
  padding-top: var(--space-3);
}

.identities-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  margin-bottom: var(--space-2);
}

.identities-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.identity-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: var(--text-sm);
}

.identity-text {
  font-weight: 500;
}

.identity-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.identity-badge {
  font-size: 10px;
  padding: 1px 6px;
  background-color: var(--color-neutral-100);
  color: var(--text-secondary);
  border-radius: var(--radius-pill);
}

.identity-badge.verified {
  background-color: #f0fdf4;
  color: var(--status-success);
}

.unbind-btn {
  border: none;
  background: none;
  font-size: 18px;
  color: var(--status-danger);
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
}

.unbind-btn:hover {
  font-weight: bold;
}

.no-identities {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.bind-form {
  display: flex;
  gap: var(--space-2);
  width: 100%;
}

.bind-input {
  flex: 1;
}

@media (max-width: 600px) {
  .create-form {
    grid-template-columns: 1fr;
  }
}
</style>