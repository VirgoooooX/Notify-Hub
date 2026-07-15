<script setup lang="ts">
import type { Plugin } from '@/types'
import StatusBadge from '@/components/StatusBadge.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppButton from '@/components/ui/AppButton.vue'

defineProps<{
  item: Plugin
  running: boolean
}>()

const emit = defineEmits<{
  (e: 'run', item: Plugin): void
  (e: 'configure', item: Plugin): void
  (e: 'toggle', item: Plugin): void
}>()

const time = (v?: string) =>
  v
    ? new Intl.DateTimeFormat('zh-CN', {
        dateStyle: 'short',
        timeStyle: 'short'
      }).format(new Date(v))
    : '—'

const scheduleText = (item: Plugin) => {
  if (!item.schedule) return '手动'
  const source = item.schedule_inherits_default ? '默认 · ' : ''
  if (item.schedule.type === 'interval') {
    const minutes = item.schedule.seconds / 60
    return `${source}每 ${Number.isInteger(minutes) ? minutes : minutes.toFixed(1)} 分钟`
  }
  return `${source}${item.schedule.expression} · ${item.schedule.timezone}`
}
</script>

<template>
  <AppCard padding="md" class="plugin-card">
    <template #header>
      <div class="card-header-wrap">
        <div>
          <h3 class="plugin-title">
            {{ item.name }}
            <small class="version-tag">{{ item.version }}</small>
          </h3>
          <span class="mono muted item-id">{{ item.id }}</span>
        </div>
        <StatusBadge :status="item.status" />
      </div>
    </template>
    
    <p class="desc-text">
      {{ item.description }}
    </p>
    
    <div class="entity-meta">
      <div class="meta-row">
        <span>调度</span>
        <strong class="mono schedule-value">{{ scheduleText(item) }}</strong>
      </div>
      <div class="meta-row">
        <span>上次运行</span>
        <strong>{{ time(item.last_run_at) }}</strong>
      </div>
      <div class="meta-row">
        <span>下次运行</span>
        <strong>{{ time(item.next_run_at) }}</strong>
      </div>
      <div class="meta-row">
        <span>连续失败</span>
        <strong :class="{ 'text-danger': item.consecutive_failures }">
          {{ item.consecutive_failures ?? 0 }}
        </strong>
      </div>
      
      <div
        v-for="secretItem in item.secrets ?? []"
        :key="secretItem.name"
        class="meta-row"
      >
        <span>{{ secretItem.name }}</span>
        <strong>
          {{ secretItem.source === 'env' ? '已通过环境变量配置' : (secretItem.configured ? '已配置' : '未配置') }}
        </strong>
      </div>
    </div>

    <template #footer>
      <div class="card-actions">
        <AppButton
          size="sm"
          variant="primary"
          :disabled="running"
          @click="emit('run', item)"
        >
          {{ running ? '已排队' : '立即运行' }}
        </AppButton>
        <AppButton size="sm" @click="emit('configure', item)">
          配置
        </AppButton>
        <AppButton size="sm" @click="emit('toggle', item)">
          {{ item.enabled ? '停用' : '启用' }}
        </AppButton>
      </div>
    </template>
  </AppCard>
</template>

<style scoped>
.plugin-card {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.card-header-wrap {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.plugin-title {
  font-size: var(--text-md);
  font-weight: 700;
  margin: 0;
}

.version-tag {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  font-weight: normal;
  margin-left: var(--space-1);
}

.item-id {
  font-size: 11px;
}

.desc-text {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  min-height: 36px;
  line-height: var(--leading-normal);
  margin: var(--space-2) 0 var(--space-4) 0;
}

.entity-meta {
  display: grid;
  gap: var(--space-2);
  padding: var(--space-3) 0;
  border-top: 1px solid var(--border-subtle);
  border-bottom: 1px solid var(--border-subtle);
  font-size: var(--text-sm);
}

.meta-row {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
}

.meta-row span {
  color: var(--text-secondary);
}

.meta-row strong {
  font-weight: 500;
}

.schedule-value {
  max-width: 70%;
  overflow-wrap: anywhere;
  text-align: right;
}

.text-danger {
  color: var(--status-danger);
}

.card-actions {
  display: flex;
  gap: var(--space-2);
  width: 100%;
}
</style>
