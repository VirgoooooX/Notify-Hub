<script setup lang="ts">
defineProps<{
  title: string
  value: string | number
  note?: string
}>()
</script>

<template>
  <div class="app-stat-card">
    <span class="stat-title">{{ title }}</span>
    <strong class="stat-value">{{ value }}</strong>
    <small v-if="note" class="stat-note">{{ note }}</small>
  </div>
</template>

<style scoped>
.app-stat-card {
  background-color: var(--surface-panel);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-panel);
  padding: var(--space-4);
  min-height: 120px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  position: relative;
  overflow: hidden;
}

.app-stat-card::before {
  content: '';
  position: absolute;
  right: -16px;
  top: -20px;
  width: 72px;
  height: 72px;
  border: 1px solid var(--border-subtle);
  transform: rotate(36deg);
  pointer-events: none;
}

.stat-title {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.stat-value {
  font-family: var(--font-mono);
  font-size: 30px;
  font-weight: 500;
  color: var(--text-primary);
  line-height: var(--leading-tight);
  margin: var(--space-2) 0;
}

.stat-note {
  font-size: var(--text-xs);
  color: var(--text-secondary);
}
</style>
