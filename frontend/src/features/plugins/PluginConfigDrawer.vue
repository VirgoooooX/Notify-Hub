<script setup lang="ts">
import { computed } from 'vue'
import type { Plugin, Person, AIProfile, PluginSecret } from '@/types'
import AppDrawer from '@/components/ui/AppDrawer.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import PluginRecipientSelector from './PluginRecipientSelector.vue'
import PluginSecretFields from './PluginSecretFields.vue'
import PluginAiDecisionFields from './PluginAiDecisionFields.vue'

const props = defineProps<{
  open: boolean
  plugin: (Plugin & { secrets?: PluginSecret[] }) | null
  people: Person[]
  aiProfiles: AIProfile[]
  busy: boolean
  
  // Form fields
  formState: {
    username: string
    twscrape_fetch_limit: number
    interval_seconds: number
    include_replies: boolean
    include_reposts: boolean
    decision_mode: string
    ai_profile: string
    ai_min_confidence: number
    rule_ai_threshold: number
    source: string
    feed_url: string
    cover_image_url: string
    fallback_cover_url: string
    recipients: string[]
    secrets: Record<string, string>
  }
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'save'): void
}>()

const allowedAiProfiles = computed(() => {
  const allowed = props.plugin?.manifest?.permissions?.ai_profiles ?? []
  return props.aiProfiles.filter((profile) => allowed.includes(profile.id))
})
</script>

<template>
  <AppDrawer
    :model-value="open"
    :title="plugin ? `配置插件 - ${plugin.name}` : '配置插件'"
    size="lg"
    @update:model-value="emit('close')"
    @close="emit('close')"
  >
    <form v-if="plugin" class="drawer-form grid" @submit.prevent="emit('save')">
      <div class="field">
        <label>监控账号</label>
        <AppInput v-model="formState.username" required />
      </div>

      <div class="field">
        <label>抓取周期（分钟）</label>
        <AppInput
          :model-value="Math.round(formState.interval_seconds / 60)"
          type="number"
          min="1"
          required
          @update:model-value="formState.interval_seconds = Number($event) * 60"
        />
      </div>

      <!-- Codex X Monitor Specific -->
      <template v-if="plugin.id === 'codex_x_monitor'">
        <div class="field">
          <label>数据源</label>
          <AppSelect v-model="formState.source">
            <option value="rss">
              RSS
            </option>
            <option value="x_api">
              X API
            </option>
            <option value="twscrape">
              twscrape
            </option>
          </AppSelect>
        </div>

        <div v-if="formState.source === 'rss'" class="field">
          <label>RSS Feed URL</label>
          <AppInput v-model="formState.feed_url" type="url" required />
        </div>

        <div v-if="formState.source === 'twscrape'" class="field">
          <label>twscrape 抓取条数</label>
          <AppInput v-model.number="formState.twscrape_fetch_limit" type="number" min="10" max="100" required />
        </div>
      </template>

      <!-- HWG Monitor Specific -->
      <template v-if="plugin.id === 'fabrizio_hwg_monitor'">
        <div class="field">
          <label>twscrape 抓取条数</label>
          <AppInput v-model.number="formState.twscrape_fetch_limit" type="number" min="10" max="100" required />
        </div>
      </template>

      <AppAlert variant="info" class="full-width">
        仅处理增量原创帖子；回复和转推会在规则与 AI 判定前过滤。
      </AppAlert>

      <!-- AI Options -->
      <template v-if="plugin.id === 'codex_x_monitor'">
        <PluginAiDecisionFields
          v-model:decision-mode="formState.decision_mode"
          v-model:ai-profile="formState.ai_profile"
          v-model:ai-min-confidence="formState.ai_min_confidence"
          v-model:rule-ai-threshold="formState.rule_ai_threshold"
          :allowed-profiles="allowedAiProfiles"
        />
      </template>

      <!-- Cover Images -->
      <div v-if="plugin.id === 'codex_x_monitor'" class="field full-width">
        <label>封面图片 URL (可选)</label>
        <AppInput v-model="formState.cover_image_url" type="url" placeholder="https://..." />
      </div>
      <div v-if="plugin.id === 'fabrizio_hwg_monitor'" class="field full-width">
        <label>回退默认封面 URL (可选)</label>
        <AppInput v-model="formState.fallback_cover_url" type="url" placeholder="https://..." />
      </div>

      <!-- Recipients -->
      <div class="full-width">
        <PluginRecipientSelector
          v-model="formState.recipients"
          :people="people"
        />
      </div>

      <!-- Secrets -->
      <div v-if="plugin.secrets && plugin.secrets.length" class="full-width">
        <PluginSecretFields
          v-model="formState.secrets"
          :secrets-list="plugin.secrets"
        />
      </div>
    </form>

    <template #footer>
      <AppButton :disabled="busy" @click="emit('close')">
        取消
      </AppButton>
      <AppButton variant="primary" :loading="busy" @click="emit('save')">
        保存配置
      </AppButton>
    </template>
  </AppDrawer>
</template>

<style scoped>
.drawer-form {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.full-width {
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

@media (max-width: 600px) {
  .drawer-form {
    grid-template-columns: 1fr;
  }
}
</style>
