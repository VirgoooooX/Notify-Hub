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

const handleChange = (event: Event) => {
  const target = event.target as HTMLInputElement
  emit('update:modelValue', target.checked)
}
</script>

<template>
  <label class="app-checkbox-container" :class="{ 'is-disabled': disabled }">
    <input
      :id="id"
      type="checkbox"
      :checked="modelValue"
      :disabled="disabled"
      class="app-checkbox-input"
      @change="handleChange"
    >
    <span class="app-checkbox-checkmark" />
    <span v-if="$slots.default" class="app-checkbox-label">
      <slot />
    </span>
  </label>
</template>

<style scoped>
.app-checkbox-container {
  display: inline-flex;
  align-items: center;
  position: relative;
  cursor: pointer;
  font-size: var(--text-sm);
  user-select: none;
  min-height: 20px;
}

.app-checkbox-input {
  position: absolute;
  opacity: 0;
  cursor: pointer;
  height: 0;
  width: 0;
}

.app-checkbox-checkmark {
  position: relative;
  height: 16px;
  width: 16px;
  background-color: #ffffff;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  transition: all 120ms ease;
  display: inline-block;
}

.app-checkbox-container:hover .app-checkbox-input:not(:disabled) ~ .app-checkbox-checkmark {
  border-color: var(--border-strong);
}

.app-checkbox-input:checked ~ .app-checkbox-checkmark {
  background-color: var(--action-primary);
  border-color: var(--action-primary);
}

.app-checkbox-checkmark:after {
  content: "";
  position: absolute;
  display: none;
}

.app-checkbox-input:checked ~ .app-checkbox-checkmark:after {
  display: block;
}

.app-checkbox-container .app-checkbox-checkmark:after {
  left: 5px;
  top: 1px;
  width: 4px;
  height: 8px;
  border: solid white;
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}

.app-checkbox-label {
  margin-left: var(--space-2);
  color: var(--text-primary);
}

.is-disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
</style>
