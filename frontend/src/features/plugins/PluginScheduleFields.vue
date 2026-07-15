<script setup lang="ts">
import { computed } from 'vue'
import type { PluginSchedule, PluginScheduleFormState } from '@/types'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'

const props = defineProps<{
  formState: PluginScheduleFormState
  defaultSchedule?: PluginSchedule
}>()

const defaultSummary = computed(() => {
  const schedule = props.defaultSchedule
  if (!schedule) return '未声明默认调度'
  if (schedule.type === 'interval') {
    const minutes = schedule.seconds / 60
    return `每 ${Number.isInteger(minutes) ? minutes : minutes.toFixed(1)} 分钟运行一次`
  }
  return `${schedule.expression} · ${schedule.timezone}`
})
</script>

<template>
  <section class="schedule-panel full-width">
    <div class="schedule-heading">
      <div>
        <span class="eyebrow">运行调度</span>
        <p>调度由平台统一管理，修改其他插件设置不会覆盖这里。</p>
      </div>
      <span class="schedule-state">{{ formState.schedule_mode === 'default' ? '跟随插件' : '自定义' }}</span>
    </div>

    <div class="schedule-grid">
      <div class="field">
        <label>调度方式</label>
        <AppSelect v-model="formState.schedule_mode">
          <option value="default">
            跟随 Manifest 默认值
          </option>
          <option value="interval">
            固定间隔
          </option>
          <option value="cron">
            Cron 表达式
          </option>
        </AppSelect>
      </div>

      <div v-if="formState.schedule_mode === 'default'" class="default-card">
        <span>当前默认值</span>
        <strong>{{ defaultSummary }}</strong>
        <small>插件升级后，默认调度发生变化时将自动跟随。</small>
      </div>

      <div v-else-if="formState.schedule_mode === 'interval'" class="field">
        <label>间隔（分钟）</label>
        <AppInput
          v-model.number="formState.schedule_interval_minutes"
          type="number"
          min="1"
          max="43200"
          required
        />
        <small>最短 1 分钟，最长 30 天。</small>
      </div>

      <template v-else>
        <div class="field cron-expression">
          <label>Cron 表达式</label>
          <AppInput
            v-model="formState.schedule_cron_expression"
            placeholder="*/10 * * * *"
            required
          />
          <small>使用 5 段格式：分 时 日 月 周，例如每 10 分钟为 */10 * * * *。</small>
        </div>
        <div class="field timezone-field">
          <label>时区</label>
          <AppInput v-model="formState.schedule_timezone" placeholder="Asia/Shanghai" required />
          <small>使用 IANA 时区名称。</small>
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.schedule-panel {
  padding: var(--space-4);
  border: 1px solid var(--border);
  background: color-mix(in srgb, var(--surface) 88%, var(--accent) 12%);
}

.schedule-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-4);
}

.schedule-heading p {
  margin: var(--space-1) 0 0;
  color: var(--text-secondary);
  font-size: var(--text-xs);
}

.eyebrow {
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-weight: 700;
}

.schedule-state {
  flex: 0 0 auto;
  padding: 3px 8px;
  border: 1px solid var(--border);
  color: var(--text-secondary);
  font-size: 11px;
}

.schedule-grid {
  display: grid;
  grid-template-columns: minmax(0, 0.8fr) minmax(0, 1.2fr);
  gap: var(--space-4);
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.field label,
.default-card span {
  color: var(--text-secondary);
  font-size: var(--text-xs);
  font-weight: 600;
}

.field small,
.default-card small {
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.5;
}

.default-card {
  display: flex;
  min-height: 68px;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  padding: 10px 12px;
  border-left: 2px solid var(--accent);
  background: var(--surface);
}

.default-card strong {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
}

.cron-expression {
  grid-column: 1 / -1;
}

.timezone-field {
  grid-column: 1 / -1;
}

@media (max-width: 600px) {
  .schedule-grid {
    grid-template-columns: 1fr;
  }
}
</style>
