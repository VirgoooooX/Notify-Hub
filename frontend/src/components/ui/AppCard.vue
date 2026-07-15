<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    hoverable?: boolean
    padding?: 'none' | 'sm' | 'md' | 'lg'
  }>(),
  {
    hoverable: false,
    padding: 'md'
  }
)

const classes = computed(() => {
  return [
    'app-card',
    `app-card-pad--${props.padding}`,
    { 'app-card--hoverable': props.hoverable }
  ]
})
</script>

<template>
  <div :class="classes">
    <div v-if="$slots.header" class="app-card-header">
      <slot name="header" />
    </div>
    <div class="app-card-body">
      <slot />
    </div>
    <div v-if="$slots.footer" class="app-card-footer">
      <slot name="footer" />
    </div>
  </div>
</template>

<style scoped>
.app-card {
  background-color: var(--surface-panel);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-panel);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.app-card--hoverable {
  transition: transform 150ms ease, box-shadow 150ms ease, border-color 150ms ease;
}

.app-card--hoverable:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 24px rgba(48, 53, 46, 0.06);
  border-color: var(--border-strong);
}

/* Padding options */
.app-card-pad--none {
  padding: 0;
}
.app-card-pad--sm {
  padding: var(--space-3);
}
.app-card-pad--md {
  padding: var(--space-5);
}
.app-card-pad--lg {
  padding: var(--space-6);
}

.app-card-header {
  border-bottom: 1px solid var(--border-subtle);
  padding-bottom: var(--space-3);
  margin-bottom: var(--space-4);
}

.app-card-footer {
  border-top: 1px solid var(--border-subtle);
  padding-top: var(--space-3);
  margin-top: var(--space-4);
}
</style>
