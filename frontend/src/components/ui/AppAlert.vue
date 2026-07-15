<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    variant?: 'success' | 'warning' | 'danger' | 'info'
    title?: string
  }>(),
  {
    variant: 'info'
  }
)

const classes = computed(() => {
  return [
    'app-alert',
    `app-alert--${props.variant}`
  ]
})
</script>

<template>
  <div :class="classes" role="alert">
    <div v-if="title" class="app-alert-title">
      {{ title }}
    </div>
    <div class="app-alert-content">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.app-alert {
  padding: var(--space-3) var(--space-4);
  border-left: 3px solid transparent;
  font-size: var(--text-sm);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}

.app-alert--info {
  border-left-color: var(--status-info);
  background-color: #f0f7ff;
  color: #1e3a8a;
}

.app-alert--success {
  border-left-color: var(--status-success);
  background-color: #f0fdf4;
  color: #14532d;
}

.app-alert--warning {
  border-left-color: var(--status-warning);
  background-color: #fffbeb;
  color: #78350f;
}

.app-alert--danger {
  border-left-color: var(--status-danger);
  background-color: #fef2f2;
  color: #7f1d1d;
}

.app-alert-title {
  font-weight: 700;
  margin-bottom: var(--space-1);
}

.app-alert-content {
  line-height: var(--leading-normal);
}
</style>
