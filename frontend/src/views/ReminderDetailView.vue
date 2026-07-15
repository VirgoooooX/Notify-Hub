<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '@/lib/api'
import type { Reminder } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import DescriptionList from '@/components/data/DescriptionList.vue'
import TimelineList from '@/components/data/TimelineList.vue'
import LoadingState from '@/components/feedback/LoadingState.vue'
import { useUiStore } from '@/stores/ui'

const route = useRoute()
const ui = useUiStore()
const item = ref<Reminder>()
const action = ref('')
const busy = ref(false)

async function load() {
  try {
    item.value = await api.get(`/admin/reminders/${route.params.id}`)
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '提醒加载失败', 'danger')
  }
}

async function execute() {
  if (!action.value) return
  busy.value = true
  try {
    await api.post(`/admin/reminders/${route.params.id}/${action.value}`)
    ui.toast('提醒状态已更新', 'success')
    action.value = ''
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '操作失败', 'danger')
  } finally {
    busy.value = false
  }
}

onMounted(load)

const time = (v?: string) =>
  v
    ? new Intl.DateTimeFormat('zh-CN', {
        dateStyle: 'medium',
        timeStyle: 'medium'
      }).format(new Date(v))
    : '—'

const labels: Record<string, string> = {
  pause: '暂停',
  resume: '恢复',
  complete: '强制完成',
  cancel: '取消'
}
</script>

<template>
  <PageHeader title="催办时间线" eyebrow="REMINDER / AUDIT TRAIL">
    <RouterLink v-slot="{ navigate }" to="/reminders" custom>
      <AppButton @click="navigate">
        返回提醒
      </AppButton>
    </RouterLink>
  </PageHeader>

  <LoadingState v-if="!item" message="LOADING REMINDER..." />

  <template v-else>
    <section class="grid detail-grid">
      <AppCard padding="md">
        <template #header>
          <div class="panel-header-wrap">
            <div>
              <h3 class="panel-title">
                {{ item.title }}
              </h3>
              <span class="mono muted reminder-id">{{ item.id }}</span>
            </div>
            <StatusBadge :status="item.status" />
          </div>
        </template>
        <p class="content-text">
          {{ item.content }}
        </p>
        <DescriptionList>
          <dt>调度类型</dt>
          <dd>{{ item.schedule_type }}</dd>
          <dt>时区</dt>
          <dd>{{ item.timezone ?? '—' }}</dd>
          <dt>下次触发</dt>
          <dd>{{ time(item.next_run_at) }}</dd>
          <dt>确认策略</dt>
          <dd>{{ item.require_ack ? item.ack_policy : '无需确认' }}</dd>
          <dt>催办进度</dt>
          <dd>{{ item.attempt_count ?? 0 }} / {{ item.max_attempts ?? '—' }}</dd>
          <dt>停止时间</dt>
          <dd>{{ time(item.stop_at) }}</dd>
        </DescriptionList>

        <div class="actions-row">
          <AppButton
            v-if="!['paused', 'completed', 'cancelled'].includes(item.status)"
            size="sm"
            @click="action = 'pause'"
          >
            暂停
          </AppButton>
          <AppButton
            v-if="item.status === 'paused'"
            variant="primary"
            size="sm"
            @click="action = 'resume'"
          >
            恢复
          </AppButton>
          <AppButton
            v-if="!['completed', 'cancelled'].includes(item.status)"
            size="sm"
            @click="action = 'complete'"
          >
            强制完成
          </AppButton>
          <AppButton
            v-if="!['completed', 'cancelled'].includes(item.status)"
            variant="danger"
            size="sm"
            @click="action = 'cancel'"
          >
            取消
          </AppButton>
        </div>
      </AppCard>

      <AppCard padding="md">
        <template #header>
          <h3 class="panel-title">
            收件人确认
          </h3>
        </template>
        <div class="recipients-list">
          <div v-for="person in item.recipients ?? []" :key="person.id" class="recipient-row">
            <span class="recipient-name">{{ person.name ?? person.id }}</span>
            <strong class="recipient-time" :class="{ 'waiting': !person.acknowledged_at }">
              {{ person.acknowledged_at ? time(person.acknowledged_at) : '等待确认' }}
            </strong>
          </div>
        </div>
      </AppCard>

      <AppCard padding="md" class="full-width">
        <template #header>
          <div class="panel-header-wrap">
            <h3 class="panel-title">
              完整时间线
            </h3>
            <span class="mono muted title-info">IDEMPOTENT CALLBACKS</span>
          </div>
        </template>
        <TimelineList>
          <li v-for="entry in item.timeline ?? []" :key="entry.id">
            <strong>{{ entry.type }}</strong>
            <span>{{ time(entry.occurred_at) }}</span>
            <p>{{ entry.message }}</p>
          </li>
        </TimelineList>
      </AppCard>
    </section>
  </template>

  <ConfirmDialog
    :open="Boolean(action)"
    :title="`${labels[action] ?? action}此提醒？`"
    :description="
      action === 'cancel'
        ? '尚未发送的投递将被取消，操作会写入审计日志。'
        : '状态转换由服务端原子执行，重复操作不会产生额外投递。'
    "
    :danger="['cancel', 'complete'].includes(action)"
    :busy="busy"
    @cancel="action = ''"
    @confirm="execute"
  />
</template>

<style scoped>
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

.reminder-id {
  font-size: 11px;
}

.content-text {
  font-size: var(--text-sm);
  line-height: var(--leading-relaxed);
  margin: 0 0 var(--space-4) 0;
}

.actions-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-5);
  padding-top: var(--space-4);
  border-top: 1px solid var(--border-subtle);
}

.recipients-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.recipient-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: var(--text-sm);
  padding-bottom: var(--space-2);
  border-bottom: 1px solid var(--border-subtle);
}

.recipient-row:last-child {
  border-bottom: none;
}

.recipient-name {
  color: var(--text-primary);
  font-weight: 500;
}

.recipient-time {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-primary);
}

.recipient-time.waiting {
  color: var(--status-warning);
  font-family: var(--font-sans);
}

.full-width {
  grid-column: 1 / -1;
}

.title-info {
  font-size: 10px;
}
</style>