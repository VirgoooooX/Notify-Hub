<script setup lang="ts">
// Slot-based flexible data table component
</script>

<template>
  <div class="data-table-wrapper">
    <table class="data-table">
      <thead>
        <tr class="data-table-header-row">
          <slot name="headers" />
        </tr>
      </thead>
      <tbody class="data-table-body">
        <slot />
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.data-table-wrapper {
  width: 100%;
  overflow-x: overlay;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background-color: var(--surface-panel);
  box-shadow: var(--shadow-panel);
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-sm);
  text-align: left;
}

:deep(th) {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 500;
  letter-spacing: 0.07em;
  color: var(--text-secondary);
  background-color: var(--color-neutral-100);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--border-default);
  text-transform: uppercase;
}

:deep(td) {
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--border-subtle);
  color: var(--text-primary);
  white-space: nowrap;
  vertical-align: middle;
}

:deep(tbody tr:last-child td) {
  border-bottom: none;
}

:deep(tbody tr:hover) {
  background-color: var(--surface-hover);
}
</style>
