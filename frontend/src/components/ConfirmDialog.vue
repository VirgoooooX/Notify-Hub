<script setup lang="ts">
import AppDialog from '@/components/ui/AppDialog.vue'
import AppButton from '@/components/ui/AppButton.vue'

defineProps<{
  open: boolean
  title: string
  description: string
  confirmText?: string
  danger?: boolean
  busy?: boolean
}>()

const emit = defineEmits<{
  confirm: []
  cancel: []
}>()
</script>

<template>
  <AppDialog
    :model-value="open"
    :title="title"
    :close-on-backdrop="!busy"
    @update:model-value="emit('cancel')"
    @close="emit('cancel')"
  >
    <p class="eyebrow-confirm">
      REQUIRES CONFIRMATION
    </p>
    <p class="desc-confirm">
      {{ description }}
    </p>
    <template #footer>
      <AppButton :disabled="busy" @click="emit('cancel')">
        取消
      </AppButton>
      <AppButton
        :variant="danger ? 'danger' : 'primary'"
        :loading="busy"
        @click="emit('confirm')"
      >
        {{ confirmText ?? '确认' }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.eyebrow-confirm {
  font-family: var(--font-sans);
  font-size: var(--text-xs);
  font-weight: 600;
  letter-spacing: 0.14em;
  color: var(--action-primary);
  margin: 0 0 var(--space-2) 0;
  text-transform: uppercase;
}
.desc-confirm {
  color: var(--text-secondary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
  margin: 0;
}
</style>