<script setup lang="ts">
defineProps<{
  content: string
}>()
</script>

<template>
  <span class="app-tooltip-container">
    <slot />
    <span class="app-tooltip-content" role="tooltip">
      {{ content }}
    </span>
  </span>
</template>

<style scoped>
.app-tooltip-container {
  position: relative;
  display: inline-flex;
}

.app-tooltip-content {
  visibility: hidden;
  position: absolute;
  bottom: 125%;
  left: 50%;
  transform: translateX(-50%);
  background-color: var(--color-neutral-900);
  color: var(--color-neutral-0);
  text-align: center;
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  line-height: var(--leading-tight);
  white-space: nowrap;
  z-index: var(--z-dropdown);
  opacity: 0;
  transition: opacity 120ms ease, visibility 120ms ease;
  box-shadow: var(--shadow-dropdown);
  pointer-events: none;
}

.app-tooltip-content::after {
  content: "";
  position: absolute;
  top: 100%;
  left: 50%;
  margin-left: -5px;
  border-width: 5px;
  border-style: solid;
  border-color: var(--color-neutral-900) transparent transparent transparent;
}

.app-tooltip-container:hover .app-tooltip-content {
  visibility: visible;
  opacity: 1;
}
</style>
