<script setup lang="ts">
import AppButton from '@/components/ui/AppButton.vue'

defineProps<{
  page: number
  pageSize: number
  total: number
}>()

const emit = defineEmits<{
  change: [page: number]
}>()
</script>

<template>
  <div class="app-pagination-bar">
    <span class="pagination-info">
      共 {{ total }} 条 · 第 {{ page }} / {{ Math.max(1, Math.ceil(total / pageSize)) }} 页
    </span>
    <div class="pagination-actions">
      <AppButton
        size="sm"
        :disabled="page <= 1"
        @click="emit('change', page - 1)"
      >
        上一页
      </AppButton>
      <AppButton
        size="sm"
        :disabled="page * pageSize >= total"
        @click="emit('change', page + 1)"
      >
        下一页
      </AppButton>
    </div>
  </div>
</template>

<style scoped>
.app-pagination-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: var(--space-4);
  color: var(--text-secondary);
  font-size: var(--text-xs);
  width: 100%;
}

.pagination-info {
  font-family: var(--font-sans);
}

.pagination-actions {
  display: flex;
  gap: var(--space-2);
}
</style>
