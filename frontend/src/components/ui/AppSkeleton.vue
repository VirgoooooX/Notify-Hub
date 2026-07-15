<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    type?: 'text' | 'title' | 'avatar' | 'card' | 'table-row'
    count?: number
  }>(),
  {
    type: 'text',
    count: 1
  }
)

const classes = computed(() => {
  return [
    'skeleton-item',
    `skeleton-${props.type}`
  ]
})
</script>

<template>
  <div class="skeleton-container">
    <div
      v-for="n in count"
      :key="n"
      :class="classes"
    />
  </div>
</template>

<style scoped>
.skeleton-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  width: 100%;
}

.skeleton-item {
  background: linear-gradient(90deg, var(--color-neutral-100) 25%, var(--color-neutral-200) 50%, var(--color-neutral-100) 75%);
  background-size: 200% 100%;
  animation: loading 1.5s infinite;
  border-radius: var(--radius-sm);
}

.skeleton-text {
  height: 14px;
  width: 100%;
}

.skeleton-title {
  height: 22px;
  width: 40%;
  margin-bottom: var(--space-2);
}

.skeleton-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
}

.skeleton-card {
  height: 120px;
  width: 100%;
}

.skeleton-table-row {
  height: 40px;
  width: 100%;
  margin-bottom: var(--space-1);
}

@keyframes loading {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}
</style>
