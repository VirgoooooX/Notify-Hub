<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    type?: 'button' | 'submit' | 'reset'
    variant?: 'primary' | 'secondary' | 'danger'
    size?: 'sm' | 'md'
    disabled?: boolean
    loading?: boolean
  }>(),
  {
    type: 'button',
    variant: 'secondary',
    size: 'md',
    disabled: false,
    loading: false
  }
)

const classes = computed(() => {
  return [
    'app-btn',
    `app-btn--${props.variant}`,
    `app-btn--${props.size}`,
    { 'app-btn--loading': props.loading }
  ]
})
</script>

<template>
  <button :type="type" :class="classes" :disabled="disabled || loading">
    <span v-if="loading" class="spinner" />
    <span class="btn-content">
      <slot />
    </span>
  </button>
</template>

<style scoped>
.app-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: 8px 14px;
  font-size: var(--text-sm);
  font-weight: 500;
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  transition: all 120ms ease;
  cursor: pointer;
  line-height: var(--leading-tight);
}

.app-btn--primary {
  background-color: var(--action-primary);
  color: var(--color-neutral-0);
  border-color: var(--action-primary);
}

.app-btn--primary:hover:not(:disabled) {
  background-color: var(--action-primary-hover);
  border-color: var(--action-primary-hover);
}

.app-btn--secondary {
  background-color: transparent;
  color: var(--text-primary);
  border-color: var(--border-default);
}

.app-btn--secondary:hover:not(:disabled) {
  background-color: var(--surface-hover);
  border-color: var(--border-strong);
}

.app-btn--danger {
  background-color: var(--action-danger);
  color: var(--color-neutral-0);
  border-color: var(--action-danger);
}

.app-btn--danger:hover:not(:disabled) {
  background-color: var(--action-danger-hover);
  border-color: var(--action-danger-hover);
}

.app-btn--sm {
  padding: 6px 10px;
  font-size: var(--text-xs);
}

.app-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid currentColor;
  border-radius: 50%;
  border-top-color: transparent;
  animation: spin 0.8s linear infinite;
}

.btn-content {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
