<script setup lang="ts">
import { useUiStore } from '@/stores/ui'
const ui = useUiStore()
</script>

<template>
  <div class="toast-stack" aria-live="polite">
    <TransitionGroup name="toast">
      <button
        v-for="item in ui.toasts"
        :key="item.id"
        class="toast"
        :class="`toast--${item.tone}`"
        @click="ui.remove(item.id)"
      >
        {{ item.message }}
      </button>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-stack {
  position: fixed;
  right: 20px;
  bottom: 20px;
  display: grid;
  gap: var(--space-2);
  z-index: var(--z-toast);
}

.toast {
  border: 0;
  border-left: 4px solid var(--status-success);
  background-color: var(--color-neutral-900);
  color: white;
  padding: 12px 16px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
  text-align: left;
  font-size: var(--text-sm);
  cursor: pointer;
  border-radius: var(--radius-sm);
}

.toast--danger {
  border-left-color: var(--status-danger);
}

.toast--info {
  border-left-color: var(--status-info);
}
</style>
