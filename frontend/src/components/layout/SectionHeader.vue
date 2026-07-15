<script setup lang="ts">
defineProps<{
  title: string
}>()
</script>

<template>
  <div class="section-header-bar">
    <h3 class="section-header-title">
      {{ title }}
    </h3>
    <div v-if="$slots.actions" class="section-header-actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.section-header-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--border-default);
  padding-bottom: var(--space-2);
  margin-bottom: var(--space-4);
}

.section-header-title {
  font-size: var(--text-md);
  font-weight: 700;
  color: var(--text-primary);
}

.section-header-actions {
  display: flex;
  gap: var(--space-2);
}
</style>
