<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    ariaLabel: string
    variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
    disabled?: boolean
  }>(),
  {
    variant: 'ghost',
    disabled: false
  }
)

const classes = computed(() => {
  return [
    'app-icon-btn',
    `app-icon-btn--${props.variant}`
  ]
})
</script>

<template>
  <button
    type="button"
    :class="classes"
    :aria-label="ariaLabel"
    :disabled="disabled"
  >
    <slot />
  </button>
</template>

<style scoped>
.app-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-2);
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  color: var(--text-secondary);
  background-color: transparent;
  transition: all 120ms ease;
  cursor: pointer;
}

.app-icon-btn:hover:not(:disabled) {
  color: var(--text-primary);
  background-color: var(--surface-hover);
}

.app-icon-btn--secondary {
  border-color: var(--border-default);
}

.app-icon-btn--primary {
  background-color: var(--action-primary);
  color: var(--color-neutral-0);
}
.app-icon-btn--primary:hover:not(:disabled) {
  background-color: var(--action-primary-hover);
  color: var(--color-neutral-0);
}

.app-icon-btn--danger {
  color: var(--action-danger);
}
.app-icon-btn--danger:hover:not(:disabled) {
  background-color: #fff1f0;
}

.app-icon-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
