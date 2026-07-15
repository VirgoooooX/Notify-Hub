<script setup lang="ts">
import { ref, computed } from 'vue'
import { Eye, EyeOff } from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    modelValue: string | number
    type?: string
    id?: string
    placeholder?: string
    disabled?: boolean
    required?: boolean
    autocomplete?: string
    error?: string | boolean
  }>(),
  {
    type: 'text',
    disabled: false,
    required: false
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'blur', event: FocusEvent): void
  (e: 'focus', event: FocusEvent): void
}>()

const showPassword = ref(false)

const computedType = computed(() => {
  if (props.type === 'password') {
    return showPassword.value ? 'text' : 'password'
  }
  return props.type
})

const handleInput = (event: Event) => {
  const target = event.target as HTMLInputElement
  emit('update:modelValue', target.value)
}
</script>

<template>
  <div class="app-input-wrapper" :class="{ 'has-error': error, 'is-disabled': disabled }">
    <input
      :id="id"
      :type="computedType"
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      :required="required"
      :autocomplete="autocomplete"
      class="app-input-field"
      @input="handleInput"
      @blur="emit('blur', $event)"
      @focus="emit('focus', $event)"
    >
    <button
      v-if="type === 'password'"
      type="button"
      class="password-toggle"
      tabindex="-1"
      :aria-label="showPassword ? 'Hide password' : 'Show password'"
      @click="showPassword = !showPassword"
    >
      <component :is="showPassword ? EyeOff : Eye" :size="16" />
    </button>
  </div>
</template>

<style scoped>
.app-input-wrapper {
  position: relative;
  display: flex;
  align-items: center;
  width: 100%;
}

.app-input-field {
  width: 100%;
  border: 1px solid var(--border-default);
  background-color: #ffffff;
  padding: 10px 12px;
  color: var(--text-primary);
  border-radius: var(--radius-sm);
  outline: none;
  font-size: var(--text-sm);
  line-height: var(--leading-tight);
  transition: border-color 120ms ease, box-shadow 120ms ease;
}

.app-input-field:focus {
  border-color: var(--border-strong);
  box-shadow: 0 0 0 3px rgba(115, 122, 112, 0.08);
}

.app-input-field::placeholder {
  color: var(--text-tertiary);
}

.has-error .app-input-field {
  border-color: var(--status-danger);
}
.has-error .app-input-field:focus {
  box-shadow: 0 0 0 3px rgba(180, 62, 50, 0.12);
}

.is-disabled .app-input-field {
  background-color: var(--surface-hover);
  color: var(--text-secondary);
  cursor: not-allowed;
}

.password-toggle {
  position: absolute;
  right: var(--space-3);
  background: none;
  border: none;
  color: var(--text-secondary);
  padding: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  border-radius: var(--radius-sm);
  transition: color 120ms ease;
}

.password-toggle:hover {
  color: var(--text-primary);
}
</style>
