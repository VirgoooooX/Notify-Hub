<script setup lang="ts">
import type { Person } from '@/types'
import AppCheckbox from '@/components/ui/AppCheckbox.vue'

const props = defineProps<{
  people: Person[]
  modelValue: string[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string[]): void
}>()

const togglePerson = (id: string, checked: boolean) => {
  const updated = [...props.modelValue]
  if (checked) {
    if (!updated.includes(id)) {
      updated.push(id)
    }
  } else {
    const idx = updated.indexOf(id)
    if (idx !== -1) {
      updated.splice(idx, 1)
    }
  }
  emit('update:modelValue', updated)
}
</script>

<template>
  <div class="recipient-selector-field">
    <label class="section-label">接收人</label>
    <div class="checkbox-group">
      <AppCheckbox
        v-for="person in people"
        :key="person.id"
        :model-value="modelValue.includes(person.id)"
        class="checkbox-item"
        @update:model-value="togglePerson(person.id, $event)"
      >
        {{ person.name }}
      </AppCheckbox>
    </div>
  </div>
</template>

<style scoped>
.recipient-selector-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.section-label {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  font-weight: 600;
  text-transform: uppercase;
}

.checkbox-group {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
  padding: var(--space-2) 0;
}

.checkbox-item {
  font-weight: normal;
}
</style>
