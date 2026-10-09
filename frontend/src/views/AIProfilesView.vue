<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '@/lib/api'
import type { AIProfile, AIInvocation, AIHubConnection } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import DataTable from '@/components/data/DataTable.vue'
import { formatInstant } from '@/lib/time'
import { useSettingsStore } from '@/stores/settings'

const settings = useSettingsStore()
const items = ref<AIProfile[]>([])
const invocations = ref<AIInvocation[]>([])
const catalogError = ref('')
const logError = ref('')
const hubUrl = ref('')
const loading = ref(false)
const labels = { classify: '分类', extract: '信息提取', summarize: '摘要 / 文章生成' }

async function load() {
  loading.value = true
  const results = await Promise.allSettled([
    api.get<AIProfile[]>('/admin/ai/profiles'),
    api.get<AIInvocation[]>('/admin/ai/invocations?limit=50'),
    api.get<AIHubConnection>('/admin/ai/hub'),
  ])
  const [catalog, logs, connection] = results
  catalogError.value = catalog.status === 'rejected' ? '无法获取 AI Hub Profile 目录，请检查中心连接。' : ''
  if (catalog.status === 'fulfilled') items.value = catalog.value
  logError.value = logs.status === 'rejected' ? '调用记录加载失败，请重试。' : ''
  if (logs.status === 'fulfilled') invocations.value = logs.value
  if (connection.status === 'fulfilled') hubUrl.value = connection.value.base_url
  loading.value = false
}
onMounted(load)
</script>

<template>
  <PageHeader title="AI Profiles" description="Profile 在 AI Hub 中统一创建和设置；这里读取可用配置，并保留 Notify Hub 的业务调用记录。">
    <AppButton :disabled="loading" @click="load">
      刷新目录与记录
    </AppButton>
    <a v-if="hubUrl" :href="hubUrl" target="_blank" rel="noopener noreferrer" class="hub-link">前往 AI Hub 管理</a>
  </PageHeader>
  <AppAlert v-if="catalogError" variant="warning">
    {{ catalogError }} 已有调用记录仍可查看。
  </AppAlert>
  <AppCard padding="md" class="table-card">
    <p class="catalog-note">
      在 AI Hub 的 Notify Hub 应用中新增 Profile，并选择用途标签 classify、extract 或 summarize；刷新后即可在对应插件中选择，无需在此重复创建。
    </p>
    <DataTable v-if="items.length" class="profile-table">
      <template #headers>
        <th>Profile</th><th>用途</th><th>生成参数</th><th>额度与缓存</th><th>状态</th>
      </template>
      <tr v-for="item in items" :key="item.id">
        <td><strong>{{ item.name }}</strong><small class="mono">{{ item.id }}</small><small>{{ item.description }}</small></td>
        <td>{{ labels[item.capability] }}</td>
        <td><span>{{ item.max_output_tokens }} Token · {{ item.timeout_seconds }} 秒</span><small>温度 {{ item.temperature }} · 推理 {{ item.reasoning_effort }}</small><small>{{ item.output_language }} · {{ item.verbosity }}</small></td>
        <td><span>{{ item.daily_request_limit ?? '不限' }} 次 / 天 · {{ item.daily_token_limit ?? '不限' }} Token</span><small>业务缓存 {{ item.cache_ttl_seconds }} 秒</small></td>
        <td><StatusBadge :status="item.enabled ? 'active' : 'disabled'" /></td>
      </tr>
    </DataTable>
    <p v-else>
      暂无可用 Profile，请在 AI Hub 配置此应用的 Profile 和用途标签。
    </p>
  </AppCard>
  <AppCard padding="md" class="invocation-panel">
    <template #header>
      <h3>最近调用</h3>
    </template>
    <p class="catalog-note">
      包含插件用途、缓存命中和业务校验结果；中心删除或停用 Profile 后，历史记录仍保留。模型路由与尝试详情可在 AI Hub 查看。
    </p>
    <AppAlert v-if="logError" variant="warning">
      {{ logError }}
    </AppAlert>
    <DataTable v-if="invocations.length">
      <template #headers>
        <th>时间</th><th>Profile</th><th>插件 / 用途</th><th>缓存</th><th>Token</th><th>延迟</th><th>状态</th>
      </template>
      <tr v-for="invocation in invocations" :key="invocation.id">
        <td>{{ formatInstant(invocation.created_at, settings.timezone) }}</td>
        <td class="mono">
          {{ invocation.profile_id }}
        </td>
        <td>{{ invocation.plugin_id ?? 'platform' }} · {{ invocation.use_case }}</td>
        <td>{{ invocation.cache_hit ? 'hit' : 'miss' }}</td>
        <td>{{ (invocation.input_tokens ?? 0) + (invocation.output_tokens ?? 0) }}</td>
        <td>{{ invocation.latency_ms ?? 0 }} ms</td>
        <td><StatusBadge :status="invocation.status" /><small v-if="invocation.error_code">{{ invocation.error_code }}</small></td>
      </tr>
    </DataTable>
    <p v-else>
      暂无调用记录。
    </p>
  </AppCard>
</template>

<style scoped>
.table-card { margin-bottom: var(--space-5); }
.catalog-note { color: var(--text-secondary); font-size: var(--text-sm); margin-bottom: var(--space-4); }
small { display: block; color: var(--text-secondary); font-size: var(--text-xs); margin-top: var(--space-1); }
.hub-link { color: var(--action-primary); font-size: var(--text-sm); }
</style>
