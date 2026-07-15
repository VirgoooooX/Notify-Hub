<script setup lang="ts">
interface SelectOption {
  value: string | number
  label: string
}

const props = withDefaults(
  defineProps<{
    modelValue: string | number
    options?: SelectOption[]
    id?: string
    disabled?: boolean
  }>(),
  {
    disabled: false
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: any): void
  (e: 'change', value: any): void
}>()

const handleChange = (event: Event) => {
  const target = event.target as HTMLSelectElement
  emit('update:modelValue', target.value)
  emit('change', target.value)
}
</script>

<template>
  <div class="app-select-wrapper" :class="{ 'is-disabled': disabled }">
    <select
      :id="id"
      :value="modelValue"
      :disabled="disabled"
      class="app-select-field"
      @change="handleChange"
    >
      <slot v-if="!options" />
      <option
        v-for="opt in options"
        v-else
        :key="opt.value"
        :value="opt.value"
      >
        {{ opt.label }}
      </option>
    </select>
  </div>
</template>

<style scoped>
.app-select-wrapper {
  position: relative;
  display: inline-flex;
  width: 100%;
}

.app-select-field {
  width: 100%;
  border: 1px solid var(--border-default);
  background-color: #ffffff;
  padding: 10px 32px 10px 12px;
  color: var(--text-primary);
  border-radius: var(--radius-sm);
  outline: none;
  font-size: var(--text-sm);
  line-height: var(--leading-tight);
  appearance: none;
  cursor: pointer;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%2371756d' stroke-width='2'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' d='M19.5 8.25l-7.5 7.5-7.5-7.5'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
  background-size: 16px;
  transition: border-color 120ms ease, box-shadow 120ms ease;
}

.app-select-field:focus {
  border-color: var(--border-strong);
  box-shadow: 0 0 0 3px rgba(115, 122, 112, 0.08);
}

.is-disabled .app-select-field {
  background-color: var(--surface-hover);
  color: var(--text-secondary);
  cursor: not-allowed;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%23deded4' stroke-width='2'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' d='M19.5 8.25l-7.5 7.5-7.5-7.5'/%3E%3C/svg%3E");
}
</style>
