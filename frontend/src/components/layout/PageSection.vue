<script setup lang="ts">
defineProps<{
  title?: string
  description?: string
}>()
</script>

<template>
  <section class="page-section">
    <header v-if="title || description" class="section-header">
      <h3 v-if="title" class="section-title">
        {{ title }}
      </h3>
      <p v-if="description" class="section-desc">
        {{ description }}
      </p>
    </header>
    <div class="section-content">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.page-section {
  margin-bottom: var(--space-6);
}

.section-header {
  margin-bottom: var(--space-4);
}

.section-title {
  font-size: var(--text-md);
  font-weight: 700;
  color: var(--text-primary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.section-desc {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin: var(--space-1) 0 0 0;
}

.section-content {
  display: grid;
  gap: var(--space-4);
}
</style>
