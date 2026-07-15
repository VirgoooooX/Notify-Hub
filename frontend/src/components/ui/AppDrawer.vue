<script setup lang="ts">
import { watch, onMounted, onUnmounted } from 'vue'
import { X } from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title?: string
    size?: 'sm' | 'md' | 'lg'
    closeOnBackdrop?: boolean
  }>(),
  {
    size: 'md',
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
      <div v-if="modelValue" class="app-drawer-backdrop" @click="handleBackdropClick">
        <Transition name="slide-right" appear>
          <div class="app-drawer" :class="`app-drawer--${size}`" role="dialog" aria-modal="true" @click.stop>
            <header class="app-drawer-header">
              <h2 v-if="title" class="app-drawer-title">
                {{ title }}
              </h2>
              <button
                type="button"
                class="app-drawer-close-btn"
                aria-label="Close drawer"
                @click="close"
              >
                <X :size="18" />
              </button>
            </header>
            <div class="app-drawer-content">
              <slot />
            </div>
            <footer v-if="$slots.footer" class="app-drawer-footer">
              <slot name="footer" />
            </footer>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.app-drawer-backdrop {
  position: fixed;
  inset: 0;
  background-color: rgba(23, 27, 24, 0.72);
  z-index: var(--z-dialog);
  display: flex;
  justify-content: flex-end;
  backdrop-filter: blur(1px);
}

.app-drawer {
  height: 100%;
  background-color: var(--surface-panel);
  box-shadow: -10px 0 30px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
  position: relative;
  border-left: 1px solid var(--border-default);
}

.app-drawer--sm {
  width: min(380px, 100%);
}

.app-drawer--md {
  width: min(520px, 100%);
}

.app-drawer--lg {
  width: min(720px, 100%);
}

.app-drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-5) var(--space-6);
  border-bottom: 1px solid var(--border-subtle);
}

.app-drawer-title {
  font-family: var(--font-sans);
  font-weight: 700;
  font-size: var(--text-lg);
  margin: 0;
}

.app-drawer-close-btn {
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

.app-drawer-close-btn:hover {
  color: var(--text-primary);
  background-color: var(--surface-hover);
}

.app-drawer-content {
  overflow-y: overlay;
  padding: var(--space-6);
  flex: 1;
}

.app-drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-6);
  border-top: 1px solid var(--border-subtle);
  background-color: var(--color-neutral-50);
}
</style>
