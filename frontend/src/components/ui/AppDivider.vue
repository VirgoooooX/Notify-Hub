<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    orientation?: 'horizontal' | 'vertical'
  }>(),
  {
    orientation: 'horizontal'
  }
)

const classes = computed(() => {
  return [
    'app-divider',
    `app-divider--${props.orientation}`
  ]
})
</script>

<template>
  <div :class="classes" role="separator" />
</template>

<style scoped>
.app-divider {
  background-color: var(--border-subtle);
}

.app-divider--horizontal {
  display: block;
  height: 1px;
  width: 100%;
  margin: var(--space-4) 0;
}

.app-divider--vertical {
  display: inline-block;
  width: 1px;
  height: 1em;
  margin: 0 var(--space-3);
  vertical-align: middle;
}
</style>
