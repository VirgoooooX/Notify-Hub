<script setup lang="ts">
// Reusable wrapper for search bar, filters dropdowns, and create buttons
</script>

<template>
  <div class="table-toolbar">
    <div class="table-toolbar-left">
      <slot name="left" />
    </div>
    <div class="table-toolbar-right">
      <slot name="right" />
    </div>
  </div>
</template>

<style scoped>
.table-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-4);
}

.table-toolbar-left {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  flex: 1;
}

.table-toolbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

@media (max-width: 600px) {
  .table-toolbar {
    flex-direction: column;
    align-items: stretch;
  }
  .table-toolbar-left, .table-toolbar-right {
    width: 100%;
  }
}
</style>
