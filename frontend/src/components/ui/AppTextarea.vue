<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    modelValue: string
    id?: string
    placeholder?: string
    disabled?: boolean
    rows?: number
  }>(),
  {
    disabled: false,
    rows: 3
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const handleInput = (event: Event) => {
  const target = event.target as HTMLTextAreaElement
  emit('update:modelValue', target.value)
}
</script>

<template>
  <textarea
    :id="id"
    :value="modelValue"
    :placeholder="placeholder"
    :disabled="disabled"
    :rows="rows"
    class="app-textarea"
    @input="handleInput"
  />
</template>

<style scoped>
.app-textarea {
  width: 100%;
  border: 1px solid var(--border-default);
  background-color: #ffffff;
  padding: 10px 12px;
  color: var(--text-primary);
  border-radius: var(--radius-sm);
  outline: none;
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
  resize: vertical;
  transition: border-color 120ms ease, box-shadow 120ms ease;
}

.app-textarea:focus {
  border-color: var(--border-strong);
  box-shadow: 0 0 0 3px rgba(115, 122, 112, 0.08);
}

.app-textarea::placeholder {
  color: var(--text-tertiary);
}

.app-textarea:disabled {
  background-color: var(--surface-hover);
  color: var(--text-secondary);
  cursor: not-allowed;
}
</style>
