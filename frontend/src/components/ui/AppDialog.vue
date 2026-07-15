<script setup lang="ts">
import { watch, onMounted, onUnmounted } from 'vue'
import { X } from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title?: string
    closeOnBackdrop?: boolean
  }>(),
  {
    closeOnBackdrop: true
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'close'): void
}>()

const close = () => {
  emit('update:modelValue', false)
  emit('close')
}

const handleBackdropClick = (event: MouseEvent) => {
  if (props.closeOnBackdrop && event.target === event.currentTarget) {
    close()
  }
}

const handleKeyDown = (event: KeyboardEvent) => {
  if (event.key === 'Escape' && props.modelValue) {
    close()
  }
}

watch(
  () => props.modelValue,
  (val) => {
    if (val) {
      document.body.style.overflow = 'hidden'
    } else {
      document.body.style.overflow = ''
    }
  }
)

onMounted(() => {
  window.addEventListener('keydown', handleKeyDown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeyDown)
  document.body.style.overflow = ''
})
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="modelValue" class="app-dialog-backdrop" @click="handleBackdropClick">
        <Transition name="dialog" appear>
          <div class="app-dialog" role="dialog" aria-modal="true" @click.stop>
            <header class="app-dialog-header">
              <h2 v-if="title" class="app-dialog-title">
                {{ title }}
              </h2>
              <button
                type="button"
                class="app-dialog-close-btn"
                aria-label="Close dialog"
                @click="close"
              >
                <X :size="18" />
              </button>
            </header>
            <div class="app-dialog-content">
              <slot />
            </div>
            <footer v-if="$slots.footer" class="app-dialog-footer">
              <slot name="footer" />
            </footer>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.app-dialog-backdrop {
  position: fixed;
  inset: 0;
  background-color: rgba(23, 27, 24, 0.72);
  z-index: var(--z-dialog);
  display: grid;
  place-items: center;
  padding: var(--space-4);
  backdrop-filter: blur(2px);
}

.app-dialog {
  width: min(460px, 100%);
  background-color: var(--surface-panel);
  padding: var(--space-6);
  border-top: 4px solid var(--action-primary);
  box-shadow: var(--shadow-dialog);
  border-radius: var(--radius-sm);
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  position: relative;
}

.app-dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-4);
}

.app-dialog-title {
  font-family: var(--font-sans);
  font-weight: 700;
  font-size: var(--text-xl);
  margin: 0;
}

.app-dialog-close-btn {
  color: var(--text-secondary);
  background: none;
  border: none;
  padding: 4px;
  cursor: pointer;
  border-radius: var(--radius-sm);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all 120ms ease;
}

.app-dialog-close-btn:hover {
  color: var(--text-primary);
  background-color: var(--surface-hover);
}

.app-dialog-content {
  overflow-y: overlay;
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: var(--leading-relaxed);
  flex: 1;
}

.app-dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-6);
}
</style>
