<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api, query } from '@/lib/api'
import type { Page, Reminder } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import PaginationBar from '@/components/PaginationBar.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppTextarea from '@/components/ui/AppTextarea.vue'
import AppCheckbox from '@/components/ui/AppCheckbox.vue'
import AppCard from '@/components/ui/AppCard.vue'
import DataTable from '@/components/data/DataTable.vue'
import TableToolbar from '@/components/data/TableToolbar.vue'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const items = ref<Page<Reminder>>({
  items: [],
  page: 1,
  page_size: 20,
  total: 0
})
const page = ref(1)
const status = ref('')
const show = ref(false)
const busy = ref(false)

const form = reactive({
  title: '',
  content: '',
  schedule_type: 'once',
  at: '',
  rrule: '',
  timezone: 'Asia/Shanghai',
  recipients: '',
  require_ack: false,
  ack_policy: 'any',
  repeat_interval_seconds: 300,
  max_attempts: 12,
  stop_at: ''
})

async function load() {
  try {
    items.value = await api.get('/admin/reminders' + query({ page: page.value, page_size: 20, status: status.value }))
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '提醒加载失败', 'danger')
  }
}

async function create() {
  busy.value = true
  try {
    await api.post('/admin/reminders', {
      title: form.title,
      content: form.content,
      schedule:
        form.schedule_type === 'once'
          ? { type: 'once', at: form.at }
          : { type: 'recurring', rrule: form.rrule, timezone: form.timezone },
      recipients: form.recipients
        .split(',')
        .map((v) => v.trim())
        .filter(Boolean),
      require_ack: form.require_ack,
      ack_policy: form.ack_policy,
      repeat: form.require_ack
        ? {
            interval_seconds: form.repeat_interval_seconds,
            max_attempts: form.max_attempts,
            stop_at: form.stop_at || undefined
          }
        : undefined
    })
    show.value = false
    ui.toast('提醒已创建', 'success')
    // Reset form fields
    form.title = ''
    form.content = ''
    form.at = ''
    form.rrule = ''
    form.recipients = ''
    form.require_ack = false
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '创建失败', 'danger')
  } finally {
    busy.value = false
  }
}

onMounted(load)

const time = (v?: string) =>
  v
    ? new Intl.DateTimeFormat('zh-CN', {
        dateStyle: 'short',
        timeStyle: 'short'
      }).format(new Date(v))
    : '—'
</script>

<template>
  <PageHeader title="提醒与催办" description="单次、周期与持续催办共享可恢复调度；确认后立即停止后续投递。">
    <AppButton variant="primary" @click="show = !show">
      {{ show ? '取消创建' : '创建提醒' }}
    </AppButton>
  </PageHeader>

  <AppCard v-if="show" padding="md" class="create-card">
    <template #header>
      <div class="panel-header-wrap">
        <h3 class="panel-title">
          新提醒
        </h3>
        <span class="mono muted">SAFE LIMITS ENFORCED</span>
      </div>
    </template>
    <form class="grid form-grid" @submit.prevent="create">
      <div class="field">
        <label>标题</label>
        <AppInput v-model="form.title" required />
      </div>
      <div class="field">
        <label>接收人 ID（逗号分隔）</label>
        <AppInput v-model="form.recipients" required />
      </div>
      <div class="field">
        <label>调度类型</label>
        <AppSelect v-model="form.schedule_type">
          <option value="once">
            单次
          </option>
          <option value="recurring">
            周期 RRULE
          </option>
        </AppSelect>
      </div>
      <div v-if="form.schedule_type === 'once'" class="field">
        <label>触发时间</label>
        <AppInput v-model="form.at" type="datetime-local" required />
      </div>
      <template v-else>
        <div class="field">
          <label>RRULE</label>
          <AppInput v-model="form.rrule" placeholder="FREQ=WEEKLY;BYDAY=MO;BYHOUR=9" required />
        </div>
        <div class="field">
          <label>时区</label>
          <AppInput v-model="form.timezone" />
        </div>
      </template>
      <div class="field full-width">
        <label>内容</label>
        <AppTextarea v-model="form.content" />
      </div>

      <div class="field checkbox-field full-width">
        <AppCheckbox v-model="form.require_ack">
          持续催办，直到确认
        </AppCheckbox>
      </div>

      <template v-if="form.require_ack">
        <div class="field">
          <label>确认策略</label>
          <AppSelect v-model="form.ack_policy">
            <option value="any">
              任一确认
            </option>
            <option value="all">
              全部确认
            </option>
            <option value="each">
              逐人确认
            </option>
          </AppSelect>
        </div>
        <div class="field">
          <label>催办间隔（秒，至少 300）</label>
          <AppInput v-model.number="form.repeat_interval_seconds" type="number" min="300" />
        </div>
        <div class="field">
          <label>最多次数</label>
          <AppInput v-model.number="form.max_attempts" type="number" min="1" max="100" />
        </div>
        <div class="field">
          <label>停止时间</label>
          <AppInput v-model="form.stop_at" type="datetime-local" />
        </div>
      </template>
      
      <div class="form-submit-row full-width">
        <AppButton type="submit" variant="primary" :loading="busy">
          立即创建
        </AppButton>
      </div>
    </form>
  </AppCard>

  <AppCard padding="md">
    <TableToolbar>
      <template #left>
        <AppSelect v-model="status" class="status-select" @change="page = 1; load()">
          <option value="">
            全部状态
          </option>
          <option value="active">
            运行中
          </option>
          <option value="awaiting_ack">
            待确认
          </option>
          <option value="paused">
            已暂停
          </option>
          <option value="completed">
            已完成
          </option>
          <option value="cancelled">
            已取消
          </option>
        </AppSelect>
      </template>
    </TableToolbar>

    <EmptyState v-if="!items.items.length" />
    
    <template v-else>
      <DataTable>
        <template #headers>
          <th>提醒</th>
          <th>调度</th>
          <th>催办进度</th>
          <th>下次触发</th>
          <th>状态</th>
        </template>
        <tr v-for="item in items.items" :key="item.id">
          <td>
            <div class="reminder-cell">
              <RouterLink class="link" :to="`/reminders/${item.id}`">
                {{ item.title }}
              </RouterLink>
              <span class="mono muted item-id">{{ item.id }}</span>
            </div>
          </td>
          <td>
            <span class="mono">
              {{ item.schedule_type }}{{ item.timezone ? ` · ${item.timezone}` : '' }}
            </span>
          </td>
          <td>
            <span>
              {{ item.require_ack ? `${item.attempt_count ?? 0} / ${item.max_attempts ?? 12}` : '无需确认' }}
            </span>
          </td>
          <td>
            <span class="time-label">{{ time(item.next_run_at) }}</span>
          </td>
          <td>
            <StatusBadge :status="item.status" />
          </td>
        </tr>
      </DataTable>

      <PaginationBar
        :page="page"
        :page-size="20"
        :total="items.total"
        @change="page = $event; load()"
      />
    </template>
  </AppCard>
</template>

<style scoped>
.create-card {
  margin-bottom: var(--space-4);
}

.panel-header-wrap {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.panel-title {
  font-size: var(--text-md);
  font-weight: 700;
  margin: 0;
}

.form-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.full-width {
  grid-column: 1 / -1;
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

.checkbox-field {
  flex-direction: row;
  align-items: center;
}

.form-submit-row {
  margin-top: var(--space-2);
}

.status-select {
  max-width: 145px;
}

.reminder-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.item-id {
  font-size: 11px;
}

.time-label {
  color: var(--text-secondary);
}

@media (max-width: 600px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
  .status-select {
    max-width: 100%;
  }
}
</style>
