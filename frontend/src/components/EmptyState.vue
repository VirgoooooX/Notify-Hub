<script setup lang="ts">
import { Inbox } from 'lucide-vue-next'

defineProps<{
  title?: string;
  description?: string;
}>()
</script>

<template>
  <div class="empty-container">
    <div class="empty-icon-wrap">
      <Inbox :size="32" />
    </div>
    <strong class="empty-title">{{ title ?? '暂无记录' }}</strong>
    <span class="empty-desc">{{ description ?? '当前筛选条件下没有数据。' }}</span>
  </div>
</template>

<style scoped>
.empty-container {
  text-align: center;
  color: var(--text-secondary);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-10) var(--space-5);
  background-color: var(--surface-panel);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-sm);
  width: 100%;
}

.empty-icon-wrap {
  color: var(--text-tertiary);
  margin-bottom: var(--space-1);
}

.empty-title {
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-weight: 600;
}

.empty-desc {
  font-size: var(--text-xs);
}
</style>
