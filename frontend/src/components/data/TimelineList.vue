<script setup lang="ts">
// Timeline wrapper component
</script>

<template>
  <ul class="app-timeline">
    <slot />
  </ul>
</template>

<style scoped>
.app-timeline {
  list-style: none;
  margin: 0;
  padding: 0;
}

:deep(li) {
  position: relative;
  padding: 0 0 var(--space-5) var(--space-6);
  border-left: 1px solid var(--border-default);
  margin-left: var(--space-2);
}

:deep(li:last-child) {
  border-left-color: transparent;
}

:deep(li:before) {
  content: '';
  position: absolute;
  left: -5px;
  top: 3px;
  width: 9px;
  height: 9px;
  background-color: var(--surface-panel);
  border: 2px solid var(--status-success);
  border-radius: 50%;
  box-shadow: 0 0 0 3px var(--surface-panel);
}

:deep(strong) {
  display: block;
  font-size: var(--text-sm);
  color: var(--text-primary);
  font-weight: 600;
}

:deep(span) {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

:deep(p) {
  font-size: var(--text-sm);
  margin: var(--space-1) 0 0 0;
  color: var(--text-secondary);
}
</style>
