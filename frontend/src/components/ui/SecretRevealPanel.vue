<script setup lang="ts">
import { ref } from 'vue'
import { Copy, Check, Eye } from 'lucide-vue-next'
import AppButton from '@/components/ui/AppButton.vue'

const props = withDefaults(
  defineProps<{
    secret: string
    title?: string
    warningText?: string
  }>(),
  {
    title: '重要安全凭据',
    warningText: '该密钥仅在此处显示一次。请立即复制并安全保存。如果丢失，您必须重新生成/轮换该密钥。'
  }
)

const emit = defineEmits<{
  (e: 'dismiss'): void
}>()

const copied = ref(false)

const copyToClipboard = async () => {
  try {
    await navigator.clipboard.writeText(props.secret)
    copied.value = true
    setTimeout(() => {
      copied.value = false
    }, 2000)
  } catch (err) {
    // Fallback
  }
}
</script>

<template>
  <div class="secret-reveal-panel">
    <div class="panel-header">
      <div class="title-wrap">
        <Eye :size="18" class="eye-icon" />
        <strong class="panel-title">{{ title }}</strong>
      </div>
    </div>
    
    <p class="warning-desc">
      {{ warningText }}
    </p>
    
    <div class="secret-box-container">
      <code class="secret-code">{{ secret }}</code>
      <AppButton
        size="sm"
        variant="primary"
        class="copy-btn"
        @click="copyToClipboard"
      >
        <component :is="copied ? Check : Copy" :size="14" />
        {{ copied ? '已复制' : '复制 Key' }}
      </AppButton>
    </div>

    <div class="panel-footer">
      <AppButton size="sm" @click="emit('dismiss')">
        我已保存
      </AppButton>
    </div>
  </div>
</template>

<style scoped>
.secret-reveal-panel {
  border: 1px solid var(--action-primary);
  background-color: #fff8f5;
  padding: var(--space-5);
  border-radius: var(--radius-sm);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  box-shadow: var(--shadow-panel);
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.title-wrap {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.eye-icon {
  color: var(--action-primary);
}

.panel-title {
  font-size: var(--text-sm);
  color: var(--text-primary);
  font-weight: 700;
}

.warning-desc {
  font-size: var(--text-xs);
  color: var(--status-warning);
  margin: 0;
  line-height: var(--leading-normal);
}

.secret-box-container {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  background-color: var(--color-neutral-900);
  border-radius: var(--radius-sm);
  padding: var(--space-3);
  overflow: hidden;
}

.secret-code {
  flex: 1;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: #fff;
  word-break: break-all;
  user-select: all;
}

.copy-btn {
  flex-shrink: 0;
}

.panel-footer {
  display: flex;
  justify-content: flex-end;
}
</style>
