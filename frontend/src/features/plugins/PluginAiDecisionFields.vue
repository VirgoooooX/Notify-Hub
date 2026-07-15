<script setup lang="ts">
import type { AIProfile } from '@/types'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppInput from '@/components/ui/AppInput.vue'

defineProps<{
  decisionMode: string
  aiProfile: string
  aiMinConfidence: number
  ruleAiThreshold: number
  allowedProfiles: AIProfile[]
}>()

const emit = defineEmits<{
  (e: 'update:decisionMode', value: string): void
  (e: 'update:aiProfile', value: string): void
  (e: 'update:aiMinConfidence', value: number): void
  (e: 'update:ruleAiThreshold', value: number): void
}>()
</script>

<template>
  <div class="ai-decision-fields">
    <div class="field">
      <label>语义判定模式</label>
      <AppSelect
        :model-value="decisionMode"
        @update:model-value="emit('update:decisionMode', $event)"
      >
        <option value="rules">
          仅规则
        </option>
        <option value="rules_then_ai">
          规则评分 + AI 兜底
        </option>
        <option value="ai">
          仅 AI
        </option>
        <option value="rules_or_ai">
          规则命中或 AI
        </option>
      </AppSelect>
    </div>

    <div class="field">
      <label>AI Profile</label>
      <AppSelect
        :model-value="aiProfile"
        :disabled="decisionMode === 'rules'"
        @update:model-value="emit('update:aiProfile', $event)"
      >
        <option v-if="!allowedProfiles.length" :value="aiProfile">
          {{ aiProfile }} (暂无可用授权 Profile)
        </option>
        <option v-for="profile in allowedProfiles" :key="profile.id" :value="profile.id">
          {{ profile.name }} · {{ profile.id }}
        </option>
      </AppSelect>
    </div>

    <div class="field">
      <label>AI 最低通知置信度</label>
      <AppInput
        :model-value="aiMinConfidence"
        type="number"
        min="0"
        max="1"
        step="0.05"
        :disabled="decisionMode === 'rules'"
        @update:model-value="emit('update:aiMinConfidence', Number($event))"
      />
    </div>

    <div class="field">
      <label>规则置信度阈值</label>
      <AppInput
        :model-value="ruleAiThreshold"
        type="number"
        min="0"
        max="1"
        step="0.05"
        :disabled="decisionMode !== 'rules_then_ai'"
        @update:model-value="emit('update:ruleAiThreshold', Number($event))"
      />
      <small>规则置信度低于该值时才调用 AI。</small>
    </div>
  </div>
</template>

<style scoped>
.ai-decision-fields {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-4);
  grid-column: 1 / -1;
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.field label {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  font-weight: 600;
}

.field small {
  color: var(--text-tertiary);
  font-size: 11px;
  line-height: 1.5;
}

@media (max-width: 600px) {
  .ai-decision-fields {
    grid-template-columns: 1fr;
  }
}
</style>
