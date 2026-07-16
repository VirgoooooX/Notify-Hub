<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '@/lib/api'
import type { ApiClient } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppCheckbox from '@/components/ui/AppCheckbox.vue'
import AppCard from '@/components/ui/AppCard.vue'
import SecretRevealPanel from '@/components/ui/SecretRevealPanel.vue'
import DataTable from '@/components/data/DataTable.vue'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const items = ref<ApiClient[]>([])
const show = ref(false)
const secret = ref('')
const target = ref<ApiClient>()
const action = ref<'rotate' | 'revoke'>('revoke')
const busy = ref(false)

const form = reactive({
  name: '',
  allowed_event_types: '',
  allowed_recipient_ids: '',
  rate_limit_per_minute: 60,
  allow_broadcast: false,
  allow_media: false,
  allow_reminders: false,
  allow_recurring: false,
  allow_cron: false,
  allow_interactive: false,
  max_active_reminders: 10
})

async function load() {
  try {
    const data = await api.get<ApiClient[] | { items: ApiClient[] }>('/admin/api-clients')
    items.value = Array.isArray(data) ? data : data.items
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Client 加载失败', 'danger')
  }
}

async function create() {
  busy.value = true
  try {
    const data = await api.post<ApiClient & { api_key: string }>('/admin/api-clients', {
      name: form.name,
      allowed_event_types: form.allowed_event_types
        .split(',')
        .map((v) => v.trim())
        .filter(Boolean),
      allowed_recipient_ids: form.allowed_recipient_ids
        .split(',')
        .map((v) => v.trim())
        .filter(Boolean),
      rate_limit_per_minute: form.rate_limit_per_minute,
      allow_broadcast: form.allow_broadcast,
      allow_media: form.allow_media,
      allow_reminders: form.allow_reminders,
      allow_recurring: form.allow_recurring,
      allow_cron: form.allow_cron,
      allow_interactive: form.allow_interactive,
      max_active_reminders: form.max_active_reminders
    })
    secret.value = data.api_key
    show.value = false
    // Reset form fields
    form.name = ''
    form.allowed_event_types = ''
    form.allowed_recipient_ids = ''
    form.rate_limit_per_minute = 60
    form.allow_broadcast = false
    form.allow_media = false
    form.allow_reminders = false
    form.allow_recurring = false
    form.allow_cron = false
    form.allow_interactive = false
    form.max_active_reminders = 10
    ui.toast('Client 已创建，请立即保存 Key', 'success')
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '创建失败', 'danger')
  } finally {
    busy.value = false
  }
}

function ask(item: ApiClient, next: 'rotate' | 'revoke') {
  target.value = item
  action.value = next
}

async function confirm() {
  if (!target.value) return
  busy.value = true
  try {
    const data = await api.post<{ api_key?: string }>(
      `/admin/api-clients/${target.value.id}/${action.value === 'rotate' ? 'rotate-key' : 'revoke'}`
    )
    if (data?.api_key) {
      secret.value = data.api_key
    }
    ui.toast(action.value === 'rotate' ? 'Key 已轮换' : 'Client 已吊销', 'success')
    target.value = undefined
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '操作失败', 'danger')
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <PageHeader title="API Clients" description="每个外部来源独立授权、限流与轮换；Key 只在创建或轮换后显示一次。">
    <AppButton variant="primary" @click="show = !show">
      {{ show ? '取消创建' : '创建 Client' }}
    </AppButton>
  </PageHeader>

  <SecretRevealPanel
    v-if="secret"
    :secret="secret"
    class="reveal-panel"
    @dismiss="secret = ''"
  />

  <AppCard v-if="show" padding="md" class="create-card">
    <template #header>
      <h3 class="panel-title">
        新 Client
      </h3>
    </template>
    <form class="create-form" @submit.prevent="create">
      <div class="field">
        <label>名称</label>
        <AppInput v-model="form.name" required />
      </div>
      <div class="field">
        <label>允许事件类型（逗号分隔）</label>
        <AppInput v-model="form.allowed_event_types" placeholder="home.alert, nas.health" />
      </div>
      <div class="field">
        <label>允许接收人 ID（逗号分隔）</label>
        <AppInput v-model="form.allowed_recipient_ids" placeholder="person_alice, person_bob" />
      </div>
      <div class="field">
        <label>每分钟限额</label>
        <AppInput v-model.number="form.rate_limit_per_minute" type="number" min="1" max="10000" />
      </div>
      <div class="form-checkbox-row">
        <AppCheckbox v-model="form.allow_broadcast">
          允许广播（高危）
        </AppCheckbox>
        <AppCheckbox v-model="form.allow_media">
          允许媒体
        </AppCheckbox>
        <AppCheckbox v-model="form.allow_reminders">
          允许创建提醒
        </AppCheckbox>
        <AppCheckbox v-model="form.allow_recurring">
          允许周期提醒
        </AppCheckbox>
        <AppCheckbox v-model="form.allow_cron">
          允许 Cron
        </AppCheckbox>
        <AppCheckbox v-model="form.allow_interactive">
          允许持续催办
        </AppCheckbox>
      </div>
      <div v-if="form.allow_reminders" class="field">
        <label>活动提醒配额</label>
        <AppInput v-model.number="form.max_active_reminders" type="number" min="1" max="1000" />
      </div>
      <div class="form-actions-row">
        <AppButton type="submit" variant="primary" :loading="busy">
          保存 Client
        </AppButton>
      </div>
    </form>
  </AppCard>

  <AppCard padding="md">
    <EmptyState v-if="!items.length" />
    
    <template v-else>
      <DataTable>
        <template #headers>
          <th>名称</th>
          <th>Key 前缀</th>
          <th>事件权限</th>
          <th>限流</th>
          <th>状态</th>
          <th>操作</th>
        </template>
        <tr v-for="item in items" :key="item.id">
          <td>
            <div class="client-cell">
              <strong class="client-name">{{ item.name }}</strong>
              <span class="mono muted item-id">{{ item.id }}</span>
            </div>
          </td>
          <td>
            <span class="mono">{{ item.key_prefix }}••••</span>
          </td>
          <td>
            <span class="allowed-types">
              {{ item.allowed_event_types?.join(', ') || '未限定' }}
            </span>
            <span v-if="item.allow_broadcast" class="danger-badge"> · 可广播</span>
            <span v-if="item.allow_reminders" class="reminder-badge">
              · 提醒 {{ item.max_active_reminders ?? 10 }} 条
            </span>
          </td>
          <td>
            <span class="mono">{{ item.rate_limit_per_minute ?? '—' }} / min</span>
          </td>
          <td>
            <StatusBadge :status="item.status" />
          </td>
          <td>
            <div class="actions-cell">
              <AppButton size="sm" @click="ask(item, 'rotate')">
                轮换
              </AppButton>
              <AppButton size="sm" @click="ask(item, 'revoke')">
                吊销
              </AppButton>
            </div>
          </td>
        </tr>
      </DataTable>
    </template>
  </AppCard>

  <ConfirmDialog
    :open="Boolean(target)"
    :title="action === 'rotate' ? '轮换 API Key？' : '吊销 Client？'"
    :description="
      action === 'rotate'
        ? '旧 Key 将立即失效，新 Key 只显示一次。'
        : '该 Client 将无法继续提交事件，此操作会进入审计日志。'
    "
    :confirm-text="action === 'rotate' ? '确认轮换' : '确认吊销'"
    danger
    :busy="busy"
    @cancel="target = undefined"
    @confirm="confirm"
  />
</template>

<style scoped>
.reveal-panel {
  margin-bottom: var(--space-4);
}

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
  grid-template-columns: repeat(3, minmax(0, 1fr));
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
  min-height: 38px;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.form-actions-row {
  grid-column: 1 / -1;
}

.client-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.client-name {
  font-size: var(--text-sm);
  color: var(--text-primary);
}

.item-id {
  font-size: 11px;
}

.allowed-types {
  font-size: var(--text-sm);
}

.danger-badge {
  color: var(--status-danger);
  font-weight: bold;
}

.reminder-badge {
  color: var(--status-info);
  font-weight: 600;
}

.actions-cell {
  display: flex;
  gap: var(--space-2);
}

@media (max-width: 768px) {
  .create-form {
    grid-template-columns: 1fr;
  }
}
</style>
