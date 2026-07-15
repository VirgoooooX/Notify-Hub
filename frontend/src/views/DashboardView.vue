<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '@/lib/api'
import type { Dashboard } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingState from '@/components/feedback/LoadingState.vue'
import StatCard from '@/components/data/StatCard.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppButton from '@/components/ui/AppButton.vue'
import TimelineList from '@/components/data/TimelineList.vue'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const loading = ref(true)
const data = ref<Dashboard>({
  today_events: 0,
  succeeded_deliveries: 0,
  failed_deliveries: 0,
  retry_wait: 0,
  failed_plugins: 0,
  recent_errors: []
})

onMounted(async () => {
  try {
    data.value = await api.get<Dashboard>('/admin/dashboard')
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : '概览加载失败', 'danger')
  } finally {
    loading.value = false
  }
})

const stats = [
  ['today_events', '今日事件', '较昨日实时累计'],
  ['succeeded_deliveries', '成功投递', '今日已完成'],
  ['failed_deliveries', '失败投递', '需要人工关注'],
  ['retry_wait', '等待重试', '由 Worker 自动恢复'],
  ['failed_plugins', '异常插件', '连续失败或降级']
] as const

const time = (v: string) =>
  new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date(v))
</script>

<template>
  <PageHeader title="运行概览" description="从事件接收到渠道投递，查看今天的系统脉搏。">
    <RouterLink v-slot="{ navigate }" to="/notifications" custom>
      <AppButton variant="primary" @click="navigate">
        检查投递链路
      </AppButton>
    </RouterLink>
  </PageHeader>

  <LoadingState v-if="loading" message="LOADING SIGNALS..." />
  
  <template v-else>
    <section class="stats-grid">
      <StatCard
        v-for="[key, label, hint] in stats"
        :key="key"
        :title="label"
        :value="data[key]"
        :note="hint"
      />
    </section>

    <section class="grid split">
      <AppCard padding="md">
        <template #header>
          <div class="panel-header-wrap">
            <h3 class="panel-title">
              投递态势
            </h3>
            <span class="mono muted">TODAY / LIVE</span>
          </div>
        </template>
        
        <div class="flow-chart-box">
          <div class="flow-steps">
            <div class="flow-step">
              <span class="step-badge">1</span>
              <span class="step-label">EVENT ACCEPTED</span>
            </div>
            <div class="flow-divider">➔</div>
            <div class="flow-step">
              <span class="step-badge">2</span>
              <span class="step-label">NOTIFICATION ROUTED</span>
            </div>
            <div class="flow-divider">➔</div>
            <div class="flow-step">
              <span class="step-badge">3</span>
              <span class="step-label">DELIVERY CLAIMED</span>
            </div>
            <div class="flow-divider">➔</div>
            <div class="flow-step">
              <span class="step-badge">4</span>
              <span class="step-label">PROVIDER ACK</span>
            </div>
          </div>
          <div class="flow-legend">
            <span class="legend-item success"><span class="dot" />核心队列工作中</span>
            <span class="legend-item warning"><span class="dot" />{{ data.retry_wait }} 条等待退避</span>
            <span class="legend-item danger"><span class="dot" />{{ data.failed_deliveries }} 条需要处理</span>
          </div>
        </div>
      </AppCard>

      <AppCard padding="md">
        <template #header>
          <div class="panel-header-wrap">
            <h3 class="panel-title">
              最近系统错误
            </h3>
            <RouterLink class="link" to="/plugins">
              查看插件
            </RouterLink>
          </div>
        </template>
        
        <EmptyState
          v-if="!data.recent_errors.length"
          title="没有新错误"
          description="最近运行状态平稳。"
        />
        <TimelineList v-else>
          <li v-for="error in data.recent_errors" :key="error.id">
            <strong>{{ error.type ?? '系统错误' }}</strong>
            <span>{{ time(error.occurred_at) }}</span>
            <p>{{ error.message }}</p>
          </li>
        </TimelineList>
      </AppCard>
    </section>
  </template>
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

.flow-chart-box {
  background-color: var(--color-neutral-50);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: var(--space-4);
}

.flow-steps {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  background-color: var(--color-neutral-100);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-default);
  margin-bottom: var(--space-4);
  overflow-x: auto;
}

.flow-step {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background-color: var(--color-neutral-0);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
}

.step-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background-color: var(--color-neutral-200);
  color: var(--text-secondary);
  font-size: 10px;
  font-weight: 700;
}

.step-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: 0.05em;
  white-space: nowrap;
}

.flow-divider {
  color: var(--color-neutral-500);
  font-size: 12px;
  user-select: none;
}

.flow-legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.legend-item .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
}

.legend-item.success .dot {
  background-color: var(--status-success);
}

.legend-item.warning .dot {
  background-color: var(--status-warning);
}

.legend-item.danger .dot {
  background-color: var(--status-danger);
}
</style>