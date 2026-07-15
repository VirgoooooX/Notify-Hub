<script setup lang="ts">
import { AlertCircle } from 'lucide-vue-next'
import AppButton from '@/components/ui/AppButton.vue'

withDefaults(
  defineProps<{
    title?: string
    message?: string
    retryText?: string
    showRetry?: boolean
  }>(),
  {
    title: '加载失败',
    message: '无法获取服务器数据，请检查网络连接或重试。',
    retryText: '重试',
    showRetry: true
  }
)

defineEmits<{
  (e: 'retry'): void
}>()
</script>

<template>
  <div class="error-state">
    <div class="error-icon">
      <AlertCircle :size="36" />
    </div>
    <h3 class="error-title">
      {{ title }}
    </h3>
    <p class="error-message">
      {{ message }}
    </p>
    <AppButton
      v-if="showRetry"
      variant="primary"
      size="sm"
      @click="$emit('retry')"
    >
      {{ retryText }}
    </AppButton>
  </div>
</template>

<style scoped>
.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: var(--space-8) var(--space-4);
  max-width: 420px;
  margin: 0 auto;
}

.error-icon {
  color: var(--status-danger);
  margin-bottom: var(--space-3);
}

.error-title {
  font-size: var(--text-lg);
  font-weight: 700;
  color: var(--text-primary);
  margin: 0 0 var(--space-2) 0;
}

.error-message {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: var(--leading-normal);
  margin: 0 0 var(--space-5) 0;
}
</style>
