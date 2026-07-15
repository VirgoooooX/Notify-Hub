<script setup lang="ts">
import AppStatus from '@/components/ui/AppStatus.vue'

defineProps<{
  status: string
}>()
</script>

<template>
  <AppStatus :class="['status', `status--${status}`]" :status="status" />
</template>
