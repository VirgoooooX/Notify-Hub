<script setup lang="ts">
withDefaults(
  defineProps<{
    message?: string
  }>(),
  {
    message: '正在加载数据...'
  }
)
</script>

<template>
  <div class="loading-state">
    <div class="spinner" />
    <span class="message">{{ message }}</span>
  </div>
</template>

<style scoped>
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-8);
  color: var(--text-secondary);
  font-size: var(--text-sm);
  gap: var(--space-3);
}

.spinner {
  width: 24px;
  height: 24px;
  border: 2px solid var(--border-default);
  border-radius: 50%;
  border-top-color: var(--action-primary);
  animation: spin 0.8s linear infinite;
}

.message {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
