<script setup lang="ts">
defineProps<{
  title?: string
  description?: string
}>()
</script>

<template>
  <div class="form-section">
    <div v-if="title || description" class="form-section-info">
      <h4 v-if="title" class="form-section-title">
        {{ title }}
      </h4>
      <p v-if="description" class="form-section-desc">
        {{ description }}
      </p>
    </div>
    <div class="form-section-fields">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.form-section {
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: var(--space-6);
  padding: var(--space-5) 0;
  border-bottom: 1px solid var(--border-subtle);
}

.form-section:last-child {
  border-bottom: none;
}

.form-section-title {
  font-size: var(--text-sm);
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
}

.form-section-desc {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin: var(--space-1) 0 0 0;
  line-height: var(--leading-normal);
}

.form-section-fields {
  display: grid;
  gap: var(--space-4);
  max-width: 600px;
}

@media (max-width: 768px) {
  .form-section {
    grid-template-columns: 1fr;
    gap: var(--space-3);
  }
}
</style>
