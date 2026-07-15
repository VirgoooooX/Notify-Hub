<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '@/lib/api'
import type { Notification, Delivery } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import DescriptionList from '@/components/data/DescriptionList.vue'
import TimelineList from '@/components/data/TimelineList.vue'
import LoadingState from '@/components/feedback/LoadingState.vue'
import { useUiStore } from '@/stores/ui'

const route = useRoute()
const ui = useUiStore()
const item = ref<Notification>()
const target = ref<Delivery>()
const busy = ref(false)

async function load() {
  try {
    item.value = await api.get<Notification>(`/admin/notifications/${route.params.id}`)
    for (const d of item.value.deliveries ?? []) {
      d.attempts = await api.get(`/admin/deliveries/${d.id}/attempts`)
    }
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '详情加载失败', 'danger')
  }
}

async function retry() {
  if (!target.value) return
  busy.value = true
  try {
    await api.post(`/admin/deliveries/${target.value.id}/retry`)
    ui.toast('投递已重新排队', 'success')
    target.value = undefined
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '重试失败', 'danger')
  } finally {
    busy.value = false
  }
}

onMounted(load)

const time = (v?: string) =>
  v
    ? new Intl.DateTimeFormat('zh-CN', {
        dateStyle: 'short',
        timeStyle: 'medium'
      }).format(new Date(v))
    : '—'
</script>

<template>
  <PageHeader
    title="通知详情"
    eyebrow="DELIVERY TRACE"
    description="供应商响应已归一化，敏感响应正文不会显示。"
  >
    <RouterLink v-slot="{ navigate }" to="/notifications" custom>
      <AppButton @click="navigate">
        返回列表
      </AppButton>
    </RouterLink>
  </PageHeader>

  <LoadingState v-if="!item" message="LOADING TRACE..." />

  <template v-else>
    <section class="grid detail-grid">
      <AppCard padding="md">
        <template #header>
          <div class="panel-header-wrap">
            <h3 class="panel-title">
              {{ item.title }}
            </h3>
            <StatusBadge :status="item.status ?? 'pending'" />
          </div>
        </template>
        <p class="content-text">
          {{ item.content }}
        </p>
        <DescriptionList>
          <dt>Notification ID</dt>
          <dd class="mono">
            {{ item.id }}
          </dd>
          <dt>消息类型</dt>
          <dd>{{ item.message_type }}</dd>
          <dt>优先级</dt>
          <dd>{{ item.priority }}</dd>
          <dt>创建时间</dt>
          <dd>{{ time(item.created_at) }}</dd>
        </DescriptionList>
      </AppCard>

      <AppCard padding="md">
        <template #header>
          <h3 class="panel-title">
            来源事件
          </h3>
        </template>
        <pre class="code-box">{{ JSON.stringify(item.event ?? {}, null, 2) }}</pre>
      </AppCard>
    </section>

    <AppCard
      v-for="delivery in item.deliveries ?? []"
      :key="delivery.id"
      padding="md"
      class="delivery-card"
    >
      <template #header>
        <div class="panel-header-wrap">
          <div>
            <h3 class="panel-title">
              {{ delivery.recipient_name ?? delivery.recipient_id ?? '未知接收人' }}
            </h3>
            <span class="mono muted delivery-id">{{ delivery.id }}</span>
          </div>
          <div class="header-actions">
            <StatusBadge :status="delivery.status" />
            <AppButton
              v-if="delivery.status === 'dead'"
              variant="danger"
              size="sm"
              @click="target = delivery"
            >
              手工重试
            </AppButton>
          </div>
        </div>
      </template>

      <AppAlert v-if="delivery.last_error_message" variant="warning" class="error-alert">
        <strong>{{ delivery.last_error_code }}</strong> · {{ delivery.last_error_message }}
      </AppAlert>

      <TimelineList class="attempts-timeline">
        <li v-for="attempt in delivery.attempts ?? []" :key="attempt.id">
          <strong>第 {{ attempt.attempt_no }} 次尝试 · {{ attempt.status }}</strong>
          <span>{{ time(attempt.started_at) }} → {{ time(attempt.finished_at) }}</span>
          <p v-if="attempt.error_message">
            {{ attempt.error_code }} · {{ attempt.error_message }}
          </p>
        </li>
      </TimelineList>
    </AppCard>
  </template>

  <ConfirmDialog
    :open="Boolean(target)"
    title="重新投递这条消息？"
    description="该操作会把 dead 投递重置为 pending，并记录一条管理员审计日志。"
    confirm-text="确认重新排队"
    danger
    :busy="busy"
    @cancel="target = undefined"
    @confirm="retry"
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

.content-text {
  font-size: var(--text-sm);
  line-height: var(--leading-relaxed);
  margin: 0 0 var(--space-4) 0;
}

.code-box {
  background-color: var(--color-neutral-900);
  color: #d9dfd7;
  padding: var(--space-4);
  font-family: var(--font-mono);
  font-size: 11px;
  overflow: overlay;
  max-height: 250px;
}

.delivery-card {
  margin-top: var(--space-4);
}

.delivery-id {
  font-size: 11px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.error-alert {
  margin-bottom: var(--space-4);
}

.attempts-timeline {
  margin-top: var(--space-4);
}
</style>