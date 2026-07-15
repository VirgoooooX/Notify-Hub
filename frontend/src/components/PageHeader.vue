<script setup lang="ts">
defineProps<{
  title: string
  eyebrow?: string
  description?: string
}>()
</script>

<template>
  <header class="app-page-header">
    <div class="header-main">
      <p class="eyebrow-text">
        {{ eyebrow ?? 'NOTIFY HUB / CONTROL' }}
      </p>
      <h1 class="title-text">
        {{ title }}
      </h1>
      <p v-if="description" class="desc-text">
        {{ description }}
      </p>
    </div>
    <div class="header-actions">
      <slot />
    </div>
  </header>
</template>

<style scoped>
.app-page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: var(--space-6);
  margin-bottom: var(--space-6);
  border-bottom: 1px solid var(--border-subtle);
  padding-bottom: var(--space-4);
}

.header-main {
  display: flex;
  flex-direction: column;
}

.eyebrow-text {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 600;
  letter-spacing: 0.14em;
  color: var(--action-primary);
  margin: 0 0 var(--space-1) 0;
  text-transform: uppercase;
}

.title-text {
  font-size: var(--text-2xl);
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
  letter-spacing: -0.02em;
}

.desc-text {
  color: var(--text-secondary);
  margin: var(--space-2) 0 0 0;
  font-size: var(--text-sm);
  max-width: 660px;
  line-height: var(--leading-normal);
}

.header-actions {
  display: flex;
  gap: var(--space-2);
}

@media (max-width: 768px) {
  .app-page-header {
    flex-direction: column;
    align-items: stretch;
    gap: var(--space-4);
  }
  .header-actions {
    width: 100%;
    justify-content: flex-start;
  }
}
</style>