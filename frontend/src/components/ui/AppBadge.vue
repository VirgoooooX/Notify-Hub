<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    variant?: 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'neutral'
  }>(),
  {
    variant: 'neutral'
  }
)

const classes = computed(() => {
  return [
    'app-badge',
    `app-badge--${props.variant}`
  ]
})
</script>

<template>
  <span :class="classes">
    <slot />
  </span>
</template>

<style scoped>
.app-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  font-size: var(--text-xs);
  font-weight: 600;
  border-radius: var(--radius-pill);
  line-height: var(--leading-tight);
}

.app-badge--neutral {
  background-color: var(--color-neutral-100);
  color: var(--text-secondary);
}

.app-badge--primary {
  background-color: #e6f1fe;
  color: var(--action-primary);
}

.app-badge--success {
  background-color: #eaf5ea;
  color: var(--status-success);
}

.app-badge--warning {
  background-color: #fdf5e8;
  color: var(--status-warning);
}

.app-badge--danger {
  background-color: #fbeee8;
  color: var(--status-danger);
}

.app-badge--info {
  background-color: #ebf3fa;
  color: var(--status-info);
}
</style>
