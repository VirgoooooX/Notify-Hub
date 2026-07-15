<script setup lang="ts">
import type { PluginSecret } from '@/types'
import AppInput from '@/components/ui/AppInput.vue'

const props = defineProps<{
  secretsList: PluginSecret[]
  modelValue: Record<string, string>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: Record<string, string>): void
}>()

const updateSecret = (name: string, value: string) => {
  const updated = { ...props.modelValue }
  updated[name] = value
  emit('update:modelValue', updated)
}
</script>

<template>
  <div class="secret-fields-container">
    <label class="section-label">密码 / Cookie 配置</label>
    <div class="secrets-list">
      <div v-for="sec in secretsList" :key="sec.name" class="secret-row-card">
        <div class="secret-meta">
          <strong class="mono secret-name">{{ sec.name }}</strong>
          <span class="secret-status-badge" :class="{ configured: sec.configured || sec.source === 'env' }">
            {{ sec.source === 'env' ? '已通过环境变量配置' : (sec.configured ? '已配置' : '未配置') }}
          </span>
        </div>
        <AppInput
          :model-value="modelValue[sec.name] || ''"
          type="password"
          :placeholder="
            sec.source === 'env'
              ? '已通过环境变量配置'
              : sec.configured
                ? '若要更新，在此输入新值；留空不修改'
                : '请输入值'
          "
          :disabled="sec.source === 'env'"
          class="secret-input"
          @update:model-value="updateSecret(sec.name, $event)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.secret-fields-container {
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

.secrets-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-top: var(--space-2);
}

.secret-row-card {
  padding: var(--space-3);
  background-color: var(--color-neutral-100);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.secret-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.secret-name {
  font-size: var(--text-sm);
}

.secret-status-badge {
  font-size: 11px;
  color: var(--text-secondary);
}

.secret-status-badge.configured {
  color: var(--status-success);
  font-weight: 500;
}

.secret-input {
  margin-top: var(--space-1);
}
</style>
