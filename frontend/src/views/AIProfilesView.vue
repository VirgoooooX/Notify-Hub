<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ApiError, api } from '@/lib/api'
import type {
  AICapability,
  AIInvocation,
  AIOutputLanguage,
  AIProfile,
  AIProvider,
  AIProviderModel,
  AIReasoningEffort,
  AIVerbosity,
} from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import DataTable from '@/components/data/DataTable.vue'
import { useUiStore } from '@/stores/ui'
import { useAsyncAction } from '@/composables/useAsyncAction'
import {
  aiProfilePolicyPayload,
  defaultAIProfileForm,
  editAIProfileForm,
} from '@/features/ai/profileForm'

const capabilityLabels: Record<AICapability, string> = {
  classify: '分类',
  extract: '信息提取',
  summarize: '摘要',
}
const languageLabels: Record<AIOutputLanguage, string> = {
  auto: '跟随输入',
  'zh-CN': '简体中文',
  en: '英文',
}
const reasoningLabels: Record<AIReasoningEffort, string> = {
  provider_default: 'Provider 默认',
  low: '低',
  medium: '中',
  high: '高',
}
const verbosityLabels: Record<AIVerbosity, string> = {
  concise: '简洁',
  standard: '标准',
  detailed: '详细',
}

const ui = useUiStore()
const items = ref<AIProfile[]>([])
const providers = ref<AIProvider[]>([])
const invocations = ref<AIInvocation[]>([])
const providerModels = ref<AIProviderModel[]>([])
const show = ref(false)
const editingId = ref<string>()
const { pending: busy, run: runSave } = useAsyncAction()
const modelsLoading = ref(false)
const deleteTarget = ref<AIProfile>()
const { pending: deleteBusy, run: runDelete } = useAsyncAction()

const form = reactive(defaultAIProfileForm())

const allowedModels = computed(() =>
  providerModels.value.filter((model) => model.available && model.enabled),
)

const deleteDescription = computed(() => {
  const name = deleteTarget.value?.name ?? '这个 Profile'
  return `将删除“${name}”的运行策略。历史调用记录会继续保留；如果启用中的插件正在使用它，删除会被拒绝，请先修改或停用相关插件。`
})

async function loadProviderModels(providerId: string) {
  const requestedProviderId = providerId
  providerModels.value = []
  modelsLoading.value = Boolean(providerId)
  if (!providerId) {
    form.model = ''
    return
  }
  try {
    const result = await api.get<{ models: AIProviderModel[] }>(
      `/admin/ai/providers/${providerId}/models`,
    )
    if (form.provider_id !== requestedProviderId) return
    providerModels.value = result.models
    if (!allowedModels.value.some((model) => model.model_id === form.model)) {
      form.model = ''
    }
  } catch (error) {
    if (form.provider_id !== requestedProviderId) return
    form.model = ''
    ui.toast(error instanceof Error ? error.message : 'Provider 模型加载失败', 'danger')
  } finally {
    if (form.provider_id === requestedProviderId) {
      modelsLoading.value = false
    }
  }
}

watch(
  () => form.provider_id,
  (providerId) => {
    void loadProviderModels(providerId)
  },
)

async function load() {
  try {
    ;[items.value, providers.value, invocations.value] = await Promise.all([
      api.get<AIProfile[]>('/admin/ai/profiles'),
      api.get<AIProvider[]>('/admin/ai/providers'),
      api.get<AIInvocation[]>('/admin/ai/invocations?limit=50'),
    ])
    if (!form.provider_id && providers.value[0]) {
      form.provider_id = providers.value[0].id
    }
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : 'AI Profile 加载失败', 'danger')
  }
}

function resetForm() {
  Object.assign(form, defaultAIProfileForm(providers.value[0]?.id))
}

function openCreate() {
  editingId.value = undefined
  resetForm()
  show.value = true
  void loadProviderModels(form.provider_id)
}

function openEdit(item: AIProfile) {
  editingId.value = item.id
  Object.assign(form, editAIProfileForm(item))
  show.value = true
  void loadProviderModels(item.provider_id)
}

function closeEditor() {
  show.value = false
  editingId.value = undefined
}

function policyPayload() {
  return aiProfilePolicyPayload(form)
}

async function saveProfile() {
  try {
    await runSave(async () => {
      if (editingId.value) {
        await api.patch(`/admin/ai/profiles/${editingId.value}`, policyPayload())
        ui.toast('Profile 运行策略已更新', 'success')
      } else {
        await api.post('/admin/ai/profiles', {
          ...policyPayload(),
          id: form.id || undefined,
          capability: form.capability,
        })
        ui.toast('Profile 已创建', 'success')
      }
    })
    closeEditor()
    await load()
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : editingId.value ? '保存失败' : '创建失败', 'danger')
  }
}

async function deleteProfile() {
  if (!deleteTarget.value) return
  try {
    await runDelete(() => api.delete(`/admin/ai/profiles/${deleteTarget.value!.id}`))
    ui.toast('Profile 已删除，历史调用记录已保留', 'success')
    deleteTarget.value = undefined
    await load()
  } catch (error) {
    const message =
      error instanceof ApiError && error.status === 409
        ? '该 Profile 正被启用中的插件使用，请先修改或停用相关插件。'
        : error instanceof Error
          ? error.message
          : '删除失败'
    ui.toast(message, 'danger')
  }
}

function providerName(id: string) {
  return providers.value.find((item) => item.id === id)?.name ?? id
}

function days(seconds: number) {
  if (seconds === 0) return '关闭'
  const value = seconds / 86400
  return `${Number.isInteger(value) ? value : value.toFixed(1)} 天`
}

onMounted(load)
</script>

<template>
  <PageHeader
    title="AI Profiles"
    description="可复用的模型运行方案：统一管理模型路由、输出风格、预算与可靠性；业务 Prompt 仍由插件提供。"
  >
    <AppButton variant="primary" class="btn--primary" :disabled="!providers.length" @click="openCreate">
      新增 Profile
    </AppButton>
  </PageHeader>

  <AppAlert v-if="!providers.length" variant="warning" class="profile-warning">
    请先创建 AI Provider，同步远端模型并明确授权可用模型。
  </AppAlert>

  <AppCard v-if="show" padding="none" class="profile-builder">
    <form @submit.prevent="saveProfile">
      <div class="profile-builder__intro">
        <div>
          <p class="eyebrow">
            RUNTIME POLICY
          </p>
          <h2>{{ editingId ? '编辑模型运行方案' : '创建模型运行方案' }}</h2>
        </div>
        <p class="intro-desc">
          Profile 决定“怎么调用”；插件决定“调用来做什么”。平台安全约束和结构化校验始终生效。
        </p>
      </div>

      <fieldset class="policy-section">
        <legend><span>01</span>身份与能力</legend>
        <div class="policy-grid">
          <div class="field">
            <label for="profile-id">稳定 ID{{ editingId ? '' : '（可选）' }}</label>
            <input
              id="profile-id"
              v-model="form.id"
              class="input mono"
              :disabled="Boolean(editingId)"
              placeholder="semantic_classifier_fast"
            >
            <small class="help-text">{{ editingId ? '插件授权与调用通过此 ID 绑定，编辑时不可更改。' : '插件通过这个 ID 引用 Profile；创建后不可更改。' }}</small>
          </div>
          <div class="field">
            <label for="profile-name">名称</label>
            <input id="profile-name" v-model="form.name" class="input" required placeholder="快速语义分类">
          </div>
          <div class="field field--full">
            <label for="profile-description">用途说明</label>
            <textarea
              id="profile-description"
              v-model="form.description"
              class="input textarea"
              rows="2"
              placeholder="用于低延迟、低成本的文本分类任务。"
            />
          </div>
          <div class="field">
            <label for="profile-capability">能力类型</label>
            <select
              id="profile-capability"
              v-model="form.capability"
              class="select"
              :disabled="Boolean(editingId)"
            >
              <option value="classify">
                分类
              </option>
              <option value="extract">
                信息提取
              </option>
              <option value="summarize">
                摘要
              </option>
            </select>
            <small class="help-text">{{ editingId ? '能力是插件调用契约的一部分；如需变更，请创建新 Profile。' : '决定插件可使用的 Gateway 方法 and 结构化输出协议。' }}</small>
          </div>
          <div class="checkbox-wrapper">
            <label class="check-card">
              <input v-model="form.enabled" type="checkbox">
              <span>
                <strong>{{ editingId ? '启用此 Profile' : '创建后立即启用' }}</strong>
                <small>停动的 Profile 不接受新的模型调用。</small>
              </span>
            </label>
          </div>
        </div>
      </fieldset>

      <fieldset class="policy-section">
        <legend><span>02</span>模型路由</legend>
        <div class="policy-grid policy-grid--three">
          <div class="field">
            <label for="profile-provider">Provider</label>
            <select id="profile-provider" v-model="form.provider_id" class="select" required>
              <option v-for="provider in providers" :key="provider.id" :value="provider.id">
                {{ provider.name }}
              </option>
            </select>
          </div>
          <div class="field field--span-two">
            <label for="ai-profile-model">已授权模型 / Deployment</label>
            <select
              id="ai-profile-model"
              v-model="form.model"
              class="select mono"
              :disabled="modelsLoading || !allowedModels.length"
              required
            >
              <option value="" disabled>
                {{ modelsLoading ? '正在加载模型…' : allowedModels.length ? '请选择已授权模型' : '没有已授权的可用模型' }}
              </option>
              <option v-for="model in allowedModels" :key="model.id" :value="model.model_id">
                {{ model.model_id }}
              </option>
            </select>
            <small v-if="!modelsLoading && form.provider_id && !allowedModels.length" class="danger">
              请先到 AI Providers 同步模型，并明确勾选允许使用的模型。
            </small>
          </div>
          <div class="field">
            <label for="profile-temperature">Temperature</label>
            <input
              id="profile-temperature"
              v-model.number="form.temperature"
              class="input"
              type="number"
              min="0"
              max="2"
              step="0.1"
            >
          </div>
          <div class="field">
            <label for="profile-max-output">最大输出 Token</label>
            <input
              id="profile-max-output"
              v-model.number="form.max_output_tokens"
              class="input"
              type="number"
              min="1"
              max="100000"
            >
          </div>
          <div class="field">
            <label for="profile-timeout">调用超时（秒）</label>
            <input
              id="profile-timeout"
              v-model.number="form.timeout_seconds"
              class="input"
              type="number"
              min="1"
              max="300"
            >
          </div>
          <div class="field">
            <label for="profile-response-format">结构化输出策略</label>
            <select id="profile-response-format" v-model="form.response_format" class="select">
              <option value="auto">
                自动协商
              </option>
              <option value="json_schema">
                JSON Schema
              </option>
              <option value="json_object">
                JSON Object
              </option>
              <option value="prompt_json">
                仅 Prompt 约束
              </option>
            </select>
          </div>
        </div>
      </fieldset>

      <fieldset class="policy-section">
        <legend><span>03</span>输出策略</legend>
        <div class="policy-grid policy-grid--three">
          <div class="field">
            <label for="profile-language">输出语言</label>
            <select id="profile-language" v-model="form.output_language" class="select">
              <option value="auto">
                跟随输入
              </option>
              <option value="zh-CN">
                简体中文
              </option>
              <option value="en">
                英文
              </option>
            </select>
          </div>
          <div class="field">
            <label for="profile-reasoning">推理强度</label>
            <select id="profile-reasoning" v-model="form.reasoning_effort" class="select">
              <option value="provider_default">
                Provider 默认
              </option>
              <option value="low">
                低
              </option>
              <option value="medium">
                中
              </option>
              <option value="high">
                高
              </option>
            </select>
          </div>
          <div class="field">
            <label for="profile-verbosity">详细程度</label>
            <select id="profile-verbosity" v-model="form.verbosity" class="select">
              <option value="concise">
                简洁
              </option>
              <option value="standard">
                标准
              </option>
              <option value="detailed">
                详细
              </option>
            </select>
          </div>
          <div class="checkbox-wrapper">
            <label class="check-card">
              <input v-model="form.include_reason" type="checkbox">
              <span>
                <strong>返回判断理由</strong>
                <small>让插件获得简短、可审计的 reason 字段。</small>
              </span>
            </label>
          </div>
          <div class="field">
            <label for="profile-reason-limit">理由最大字符数</label>
            <input
              id="profile-reason-limit"
              v-model.number="form.max_reason_characters"
              class="input"
              type="number"
              min="1"
              :disabled="!form.include_reason"
            >
          </div>
          <div class="field field--full">
            <label for="profile-system-instructions">系统补充指令</label>
            <textarea
              id="profile-system-instructions"
              v-model="form.system_instructions"
              class="input textarea mono"
              rows="4"
              placeholder="例如：判断要保守，证据不足时返回 uncertain。"
            />
            <div class="constraint-note">
              <strong>只会追加，不会替换。</strong>
              该内容会追加到平台强制安全约束之后，不能覆盖注入防护、工具禁用或输出 Schema。
            </div>
          </div>
        </div>
      </fieldset>

      <fieldset class="policy-section">
        <legend><span>04</span>成本可靠性</legend>
        <div class="policy-grid policy-grid--three">
          <div class="field">
            <label for="profile-cache">缓存 TTL（秒）</label>
            <input
              id="profile-cache"
              v-model.number="form.cache_ttl_seconds"
              class="input"
              type="number"
              min="0"
              max="31536000"
            >
            <small class="help-text">设置为 0 可关闭持久化结果缓存。</small>
          </div>
          <div class="field">
            <label for="profile-request-limit">每日请求上限</label>
            <input
              id="profile-request-limit"
              v-model.number="form.daily_request_limit"
              class="input"
              type="number"
              min="1"
              max="1000000"
            >
            <small class="help-text">留空表示无限制。</small>
          </div>
          <div class="field">
            <label for="profile-token-limit">每日 Token 上限</label>
            <input
              id="profile-token-limit"
              v-model.number="form.daily_token_limit"
              class="input"
              type="number"
              min="1"
              max="1000000000"
            >
            <small class="help-text">留空表示无限制。</small>
          </div>
        </div>
      </fieldset>

      <div class="profile-builder__footer">
        <span class="muted footer-note">{{ editingId ? '保存后新调用立即使用更新后的策略；稳定 ID、能力与历史调用记录保持不变。' : '创建后插件只需要引用稳定 Profile ID，不会接触 Provider 地址、API Key 或模型参数。' }}</span>
        <div class="footer-actions">
          <AppButton :disabled="busy" @click="closeEditor">
            取消
          </AppButton>
          <AppButton
            variant="primary"
            type="submit"
            :loading="busy"
            :disabled="busy || modelsLoading || !form.model"
          >
            {{ editingId ? '保存更改' : '创建 Profile' }}
          </AppButton>
        </div>
      </div>
    </form>
  </AppCard>

  <EmptyState v-if="!items.length" />
  
  <AppCard v-else padding="none" class="table-card">
    <div class="table-wrap profile-table">
      <DataTable>
        <template #headers>
          <th>Profile</th>
          <th>能力</th>
          <th>模型路由</th>
          <th>输出策略</th>
          <th>成本策略</th>
          <th>状态</th>
          <th>操作</th>
        </template>
        <tr v-for="item in items" :key="item.id">
          <td>
            <div class="profile-cell">
              <strong>{{ item.name }}</strong>
              <span class="mono muted item-id">{{ item.id }}</span>
              <small v-if="item.description" class="table-description">{{ item.description }}</small>
            </div>
          </td>
          <td>
            <span class="policy-tag">{{ capabilityLabels[item.capability] }}</span>
          </td>
          <td>
            <div class="route-cell">
              <span>{{ providerName(item.provider_id) }}</span>
              <span class="mono muted item-model">{{ item.model }}</span>
            </div>
          </td>
          <td>
            <div class="policy-detail-cell">
              <span>{{ languageLabels[item.output_language] }} · {{ verbosityLabels[item.verbosity] }}</span>
              <span class="muted font-xs">推理 {{ reasoningLabels[item.reasoning_effort] }} · {{ item.include_reason ? `理由 ≤ ${item.max_reason_characters} 字` : '无理由' }}</span>
            </div>
          </td>
          <td>
            <div class="budget-cell">
              <span>{{ item.daily_request_limit ?? '无限制' }} 次 / 天</span>
              <span class="muted font-xs">{{ item.daily_token_limit ?? '无限制' }} Token · 缓存 {{ days(item.cache_ttl_seconds) }}</span>
            </div>
          </td>
          <td>
            <div class="status-cell">
              <StatusBadge :status="item.enabled ? 'active' : 'disabled'" />
              <span class="muted font-xs">r{{ item.revision }}</span>
            </div>
          </td>
          <td>
            <div class="actions-cell">
              <AppButton size="sm" @click="openEdit(item)">
                编辑
              </AppButton>
              <AppButton variant="danger" size="sm" class="btn--danger" @click="deleteTarget = item">
                删除
              </AppButton>
            </div>
          </td>
        </tr>
      </DataTable>
    </div>
  </AppCard>

  <AppCard padding="md" class="invocation-panel">
    <template #header>
      <div class="panel-header-wrap">
        <h3 class="panel-title">
          最近调用
        </h3>
        <span class="muted font-xs">删除 Profile 后仍保留历史记录；不保存正文、Prompt 或凭据</span>
      </div>
    </template>
    
    <div v-if="invocations.length" class="table-wrap">
      <DataTable>
        <template #headers>
          <th>时间</th>
          <th>Profile</th>
          <th>插件 / 用途</th>
          <th>缓存</th>
          <th>Token</th>
          <th>延迟</th>
          <th>状态</th>
        </template>
        <tr v-for="invocation in invocations" :key="invocation.id">
          <td>{{ new Date(invocation.created_at).toLocaleString() }}</td>
          <td>
            <span class="mono">{{ invocation.profile_id }}</span>
          </td>
          <td>
            <span>{{ invocation.plugin_id ?? 'platform' }} · {{ invocation.use_case }}</span>
          </td>
          <td>
            <span class="mono">{{ invocation.cache_hit ? 'hit' : 'miss' }}</span>
          </td>
          <td>
            <span class="mono">{{ (invocation.input_tokens ?? 0) + (invocation.output_tokens ?? 0) }}</span>
          </td>
          <td>
            <span class="mono">{{ invocation.latency_ms ?? 0 }} ms</span>
          </td>
          <td>
            <StatusBadge :status="invocation.status" />
          </td>
        </tr>
      </DataTable>
    </div>
    <div v-else class="muted no-invocations">
      暂无调用记录。
    </div>
  </AppCard>

  <ConfirmDialog
    :open="Boolean(deleteTarget)"
    title="删除 AI Profile？"
    :description="deleteDescription"
    confirm-text="确认删除"
    danger
    :busy="deleteBusy"
    @cancel="deleteTarget = undefined"
    @confirm="deleteProfile"
  />
</template>

<style scoped>
.profile-warning,
.profile-builder {
  margin-bottom: var(--space-4);
}

.profile-builder {
  border-top: 3px solid var(--action-primary);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.profile-builder__intro {
  padding: var(--space-5) var(--space-6);
  background-color: var(--color-neutral-100);
  border-bottom: 1px solid var(--border-default);
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--space-4);
}

.profile-builder__intro h2 {
  font-size: var(--text-xl);
  font-weight: 700;
  margin: 0;
}

.intro-desc {
  max-width: 620px;
  margin: 0;
  color: var(--text-secondary);
  font-size: var(--text-xs);
  line-height: var(--leading-normal);
}

.policy-section {
  margin: 0;
  padding: var(--space-5) var(--space-6) var(--space-6);
  border: 0;
  border-bottom: 1px solid var(--border-default);
}

.policy-section legend {
  width: 100%;
  padding: 0 0 var(--space-3);
  font-weight: 700;
  font-size: var(--text-md);
  color: var(--text-primary);
  border-bottom: 1px solid var(--border-subtle);
  margin-bottom: var(--space-4);
}

.policy-section legend span {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 20px;
  margin-right: var(--space-2);
  background-color: var(--color-neutral-900);
  color: #fff;
  font-family: var(--font-mono);
  font-size: 10px;
}

.policy-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4) var(--space-5);
}

.policy-grid--three {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.field--span-two {
  grid-column: span 2;
}

.field--full {
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

.help-text {
  display: block;
  margin-top: 2px;
  color: var(--text-secondary);
  font-size: 10px;
  line-height: var(--leading-tight);
}

.help-text.danger {
  color: var(--status-danger);
}

.checkbox-wrapper {
  display: flex;
  align-items: center;
}

.check-card {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--border-default);
  background-color: var(--surface-hover);
  cursor: pointer;
  border-radius: var(--radius-sm);
  width: 100%;
}

.check-card input {
  margin-top: 3px;
}

.check-card span {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.check-card strong {
  font-size: var(--text-sm);
  color: var(--text-primary);
}

.check-card small {
  color: var(--text-secondary);
  font-size: 10px;
}

.constraint-note {
  margin-top: var(--space-2);
  padding: var(--space-3);
  border-left: 3px solid var(--action-primary);
  background-color: #fff6ee;
  color: var(--text-secondary);
  font-size: var(--text-xs);
  line-height: var(--leading-normal);
}

.constraint-note strong {
  color: var(--text-primary);
}

.profile-builder__footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-4) var(--space-6);
  background-color: var(--surface-hover);
  border: 0;
  font-size: var(--text-xs);
  gap: var(--space-4);
}

.footer-actions {
  display: flex;
  gap: var(--space-2);
  flex: none;
}

.profile-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.item-id {
  font-size: 11px;
}

.table-description {
  display: block;
  max-width: 240px;
  margin-top: var(--space-1);
  color: var(--text-secondary);
  font-size: 10px;
  line-height: var(--leading-normal);
}

.policy-tag {
  display: inline-block;
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--border-default);
  background-color: var(--color-neutral-100);
  font-family: var(--font-mono);
  font-size: 10px;
  border-radius: var(--radius-sm);
}

.route-cell,
.policy-detail-cell,
.budget-cell,
.status-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.font-xs {
  font-size: var(--text-xs);
}

.item-model {
  font-size: var(--text-xs);
}

.actions-cell {
  display: flex;
  gap: var(--space-1);
}

.table-card {
  margin-bottom: var(--space-5);
}

.invocation-panel {
  margin-top: var(--space-5);
}

.panel-header-wrap {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.panel-title {
  font-size: var(--text-md);
  font-weight: 700;
  margin: 0;
}

.no-invocations {
  padding: var(--space-5) 0;
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

@media (max-width: 1000px) {
  .policy-grid--three {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .field--span-two {
    grid-column: span 1;
  }
}

@media (max-width: 760px) {
  .profile-builder__intro {
    flex-direction: column;
    align-items: stretch;
    gap: var(--space-2);
  }
  
  .profile-builder__footer {
    flex-direction: column;
    align-items: stretch;
  }
  
  .footer-actions {
    justify-content: flex-end;
  }
  
  .policy-grid,
  .policy-grid--three {
    grid-template-columns: 1fr;
  }
  
  .field--span-two,
  .field--full {
    grid-column: auto;
  }
  
  .policy-section {
    padding-inline: var(--space-4);
  }
}
</style>
