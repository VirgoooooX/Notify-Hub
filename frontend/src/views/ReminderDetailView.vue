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
import InteractiveReminderPreview from '@/components/reminders/InteractiveReminderPreview.vue'

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


const scheduleLabel = (reminder: Reminder) => {
  if (reminder.schedule_type === 'once') return '单次提醒'
  if (reminder.schedule_type === 'interval') {
    const seconds = Number(reminder.schedule_config?.seconds ?? 0)
    return seconds ? `每 ${Math.round(seconds / 60)} 分钟` : '固定间隔'
  }
  if (reminder.schedule_type === 'cron') return 'Cron 日历计划'
  return '周期规则'
}

const contentLabel = (type?: string) => {
  if (type === 'image') return '普通图片 + 文字'
  if (type === 'article') return '普通图文消息'
  return '普通文字消息'
}
</script>

<template>
  <PageHeader title="提醒详情与催办" eyebrow="REMINDER / DELIVERY / MENU">
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
          <dd>{{ scheduleLabel(item) }}</dd>
          <dt>时区</dt>
          <dd>{{ item.timezone ?? '—' }}</dd>
          <dt>下次触发</dt>
          <dd>{{ time(item.next_run_at) }}</dd>
          <dt>确认策略</dt>
          <dd>{{ item.require_ack ? item.ack_policy : '无需确认' }}</dd>
          <dt>送达方式</dt>
          <dd>{{ contentLabel(item.content_type) }}</dd>
          <dt>交互入口</dt>
          <dd>{{ item.require_ack ? '底部【快捷操作】菜单' : '无交互 · 普通通知' }}</dd>
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
          <div>
            <h3 class="panel-title">
              收件人与交互目标
            </h3>
            <p class="panel-subtitle">
              菜单目标按企业微信 UserID 独立维护。
            </p>
          </div>
        </template>
        <div class="recipients-list">
          <div v-for="person in item.recipients ?? []" :key="person.id" class="recipient-row">
            <span class="recipient-name">{{ person.name ?? person.id }}</span>
            <div class="recipient-state">
              <strong class="recipient-time" :class="{ 'waiting': !person.acknowledged_at }">
                {{ person.acknowledged_at ? time(person.acknowledged_at) : (item.require_ack ? '等待菜单操作' : '普通送达') }}
              </strong>
              <span v-if="person.attempt_count" class="mono recipient-count">已提醒 {{ person.attempt_count }} 次</span>
            </div>
          </div>
        </div>
      </AppCard>

      <AppCard padding="md" class="full-width interaction-card">
        <template #header>
          <div class="panel-header-wrap">
            <div>
              <h3 class="panel-title">
                消息与交互方式
              </h3>
              <p class="panel-subtitle">
                这是用户实际看到的消息形态；模板卡片仅保留历史兼容。
              </p>
            </div>
            <span class="interaction-state" :class="{ active: item.require_ack }">
              {{ item.require_ack ? 'LATEST MENU TARGET' : 'PASSIVE NOTICE' }}
            </span>
          </div>
        </template>
        <div class="interaction-layout">
          <InteractiveReminderPreview
            :title="item.title"
            :content="item.content"
            :content-type="item.content_type"
            :interactive="item.require_ack"
            compact
          />
          <aside class="pointer-rules">
            <span class="rule-number mono">TARGET RULE / 01</span>
            <h4>最近成功送达的一条</h4>
            <p v-if="item.require_ack">
              每个 UserID 的菜单只操作最近成功发送的交互式 occurrence。新提醒覆盖旧指针，目标失效后不会回退。
            </p>
            <p v-else>
              这是一条普通通知，不会覆盖用户当前可操作的交互提醒。
            </p>
            <ul>
              <li>失败投递不更新目标</li>
              <li>操作确认消息不更新目标</li>
              <li>每次结果都应包含任务名称</li>
            </ul>
          </aside>
        </div>
      </AppCard>

      <AppCard padding="md" class="full-width">
        <template #header>
          <div class="panel-header-wrap">
            <h3 class="panel-title">
              提醒触发记录
            </h3>
            <span class="mono muted title-info">OCCURRENCE / RECIPIENT / TARGET</span>
          </div>
        </template>
        <div v-if="!item.occurrences || !item.occurrences.length" class="empty-occurrences mono muted text-xs">
          暂无触发记录
        </div>
        <div v-else class="occurrences-timeline">
          <div v-for="occ in item.occurrences" :key="occ.id" class="occurrence-box">
            <div class="occurrence-header">
              <span class="mono text-xs text-secondary">ID: {{ occ.id }}</span>
              <span class="occurrence-time mono">计划触发: {{ time(occ.scheduled_for) }} · 实际触发: {{ time(occ.triggered_at) }}</span>
              <StatusBadge :status="occ.status" />
            </div>
            <div class="occurrence-body">
              <div v-if="occ.title !== item.title || occ.content !== item.content" class="occurrence-snapshot">
                <strong>快照内容:</strong> {{ occ.title }} - {{ occ.content }}
              </div>
              <div class="occurrence-recipients">
                <div v-for="orc in occ.recipients" :key="orc.person_id" class="orc-row text-xs">
                  <span class="orc-person-name">{{ orc.name || orc.person_id }}</span>
                  <span class="orc-badge" :class="orc.status">{{ orc.status }}</span>
                  <span v-if="orc.latest_interactive_user_ids?.length" class="latest-target-badge">
                    当前菜单目标 · {{ orc.latest_interactive_user_ids.join(', ') }}
                  </span>
                  <span v-if="orc.acknowledged_at" class="mono text-secondary">确认时间: {{ time(orc.acknowledged_at) }}</span>
                  <span v-else-if="occ.status === 'active'" class="mono text-secondary">已通知次数: {{ orc.notify_count }} · 下次通知: {{ time(orc.next_notify_at) }}</span>
                </div>
              </div>
            </div>
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

.panel-subtitle {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: var(--text-xs);
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

.recipient-state {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 3px;
}

.recipient-count {
  color: var(--text-tertiary);
  font-size: 10px;
}

.full-width {
  grid-column: 1 / -1;
}

.title-info {
  font-size: 10px;
}

.interaction-state {
  padding: 5px 8px;
  border-radius: var(--radius-sm);
  background: rgba(113, 117, 109, 0.1);
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.08em;
}

.interaction-state.active {
  background: rgba(31, 107, 79, 0.1);
  color: #1f6b4f;
}

.interaction-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(250px, 0.75fr);
  gap: var(--space-4);
  align-items: stretch;
}

.pointer-rules {
  padding: var(--space-5);
  border: 1px solid rgba(31, 107, 79, 0.18);
  border-radius: var(--radius-lg);
  background:
    linear-gradient(160deg, rgba(31, 107, 79, 0.09), transparent 58%),
    #f8f7f0;
}

.rule-number {
  color: #1f6b4f;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.14em;
}

.pointer-rules h4 {
  margin: var(--space-3) 0 var(--space-2);
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-size: var(--text-xl);
}

.pointer-rules p,
.pointer-rules li {
  color: #5e6963;
  font-size: var(--text-xs);
  line-height: 1.75;
}

.pointer-rules ul {
  margin: var(--space-4) 0 0;
  padding: var(--space-3) 0 0 18px;
  border-top: 1px dashed rgba(31, 107, 79, 0.24);
}

.occurrences-timeline {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.occurrence-box {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  background: var(--bg-surface-secondary, rgba(0,0,0,0.01));
}

.occurrence-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--border-subtle);
  padding-bottom: var(--space-2);
  margin-bottom: var(--space-2);
}

.occurrence-time {
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

.occurrence-snapshot {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  background: rgba(0,0,0,0.03);
  padding: 4px var(--space-2);
  border-radius: 4px;
  margin-bottom: var(--space-2);
}

.occurrence-recipients {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.orc-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.latest-target-badge {
  padding: 2px 7px;
  border: 1px solid rgba(31, 107, 79, 0.2);
  border-radius: var(--radius-pill);
  background: rgba(31, 107, 79, 0.08);
  color: #1f6b4f;
  font-size: 10px;
  font-weight: 700;
}

.orc-person-name {
  font-weight: 500;
}

.orc-badge {
  font-size: 10px;
  font-weight: bold;
  text-transform: uppercase;
  padding: 1px 6px;
  border-radius: 4px;
}

.orc-badge.pending {
  background: rgba(245, 158, 11, 0.1);
  color: #d97706;
}

.orc-badge.acknowledged {
  background: rgba(16, 185, 129, 0.1);
  color: #059669;
}

.orc-badge.expired {
  background: rgba(107, 114, 128, 0.1);
  color: #4b5563;
}

.orc-badge.cancelled {
  background: rgba(239, 68, 68, 0.1);
  color: #dc2626;
}

@media (max-width: 880px) {
  .interaction-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 620px) {
  .panel-header-wrap,
  .occurrence-header,
  .recipient-row {
    align-items: flex-start;
    flex-direction: column;
    gap: var(--space-2);
  }

  .recipient-state {
    align-items: flex-start;
  }
}
</style>
