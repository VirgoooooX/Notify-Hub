<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  status: string
  label?: string
}>()

const labels: Record<string, string> = {
  active: '正常',
  enabled: '已启用',
  disabled: '已停用',
  pending: '待处理',
  processing: '处理中',
  succeeded: '成功',
  retry_wait: '等待重试',
  dead: '已终止',
  cancelled: '已取消',
  paused: '已暂停',
  completed: '已完成',
  failed: '失败',
  degraded: '降级',
  awaiting_ack: '待确认'
}

const computedLabel = computed(() => {
  return props.label ?? labels[props.status] ?? props.status
})

const dotClass = computed(() => {
  return `dot--${props.status}`
})
</script>

<template>
  <span class="app-status">
    <span class="status-dot" :class="dotClass" />
    <span class="status-label">{{ computedLabel }}</span>
  </span>
</template>

<style scoped>
.app-status {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  line-height: var(--leading-tight);
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background-color: var(--text-tertiary);
  display: inline-block;
}

/* Green states */
.dot--active,
.dot--enabled,
.dot--succeeded,
.dot--completed {
  background-color: var(--status-success);
}

/* Red states */
.dot--failed,
.dot--dead {
  background-color: var(--status-danger);
}

/* Amber states */
.dot--retry_wait,
.dot--pending,
.dot--awaiting_ack {
  background-color: var(--status-warning);
}

/* Blue pulsing state */
.dot--processing {
  background-color: var(--status-info);
  box-shadow: 0 0 0 4px rgba(61, 108, 160, 0.18);
  animation: pulse 1.8s infinite;
}

@keyframes pulse {
  0% {
    box-shadow: 0 0 0 0px rgba(61, 108, 160, 0.3);
  }
  70% {
    box-shadow: 0 0 0 5px rgba(61, 108, 160, 0);
  }
  100% {
    box-shadow: 0 0 0 0px rgba(61, 108, 160, 0);
  }
}
</style>
