<script setup lang="ts">
// Renders keys (<dt>) and values (<dd>) cleanly in a grid format
</script>

<template>
  <dl class="app-description-list">
    <slot />
  </dl>
</template>

<style scoped>
.app-description-list {
  display: grid;
  grid-template-columns: 160px 1fr;
  margin: 0;
  font-size: var(--text-sm);
  background-color: var(--surface-panel);
}

:deep(dt) {
  grid-column-start: 1;
  color: var(--text-secondary);
  font-weight: 500;
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--border-subtle);
  margin: 0;
}

:deep(dd) {
  grid-column-start: 2;
  color: var(--text-primary);
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--border-subtle);
  margin: 0;
  word-break: break-all;
  white-space: pre-wrap;
}

:deep(div:last-of-type dt),
:deep(div:last-of-type dd),
:deep(dt:last-of-type),
:deep(dd:last-of-type) {
  border-bottom: none;
}

@media (max-width: 600px) {
  .app-description-list {
    grid-template-columns: 1fr;
  }
  
  :deep(dt) {
    padding-bottom: 2px;
    border-bottom: none;
  }
  
  :deep(dd) {
    padding-top: 2px;
    padding-bottom: var(--space-3);
  }
}
</style>
