<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    modelValue: boolean
    id?: string
    disabled?: boolean
  }>(),
  {
    disabled: false
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

const toggle = () => {
  if (!props.disabled) {
    emit('update:modelValue', !props.modelValue)
  }
}
</script>

<template>
  <button
    :id="id"
    type="button"
    role="switch"
    :aria-checked="modelValue"
    :disabled="disabled"
    class="app-switch"
    :class="{ 'is-checked': modelValue, 'is-disabled': disabled }"
    @click="toggle"
  >
    <span class="app-switch-thumb" />
  </button>
</template>

<style scoped>
.app-switch {
  position: relative;
  display: inline-flex;
  height: 20px;
  width: 38px;
  flex-shrink: 0;
  cursor: pointer;
  border-radius: var(--radius-pill);
  border: 1px solid var(--border-default);
  background-color: var(--border-subtle);
  transition: background-color 150ms ease, border-color 150ms ease;
  padding: 0;
  outline: none;
}

.app-switch:focus-visible {
  outline: 2px solid var(--border-focus);
  outline-offset: 2px;
}

.app-switch.is-checked {
  background-color: var(--status-success);
  border-color: var(--status-success);
}

.app-switch-thumb {
  pointer-events: none;
  display: inline-block;
  height: 16px;
  width: 16px;
  border-radius: 50%;
  background-color: #ffffff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  transform: translateX(1px) translateY(1px);
  transition: transform 150ms cubic-bezier(0.25, 0.8, 0.25, 1);
}

.app-switch.is-checked .app-switch-thumb {
  transform: translateX(19px) translateY(1px);
}

.is-disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
</style>
