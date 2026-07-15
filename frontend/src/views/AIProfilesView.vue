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
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useUiStore } from '@/stores/ui'

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
const busy = ref(false)
const modelsLoading = ref(false)
const deleteTarget = ref<AIProfile>()
const deleteBusy = ref(false)
const form = reactive({
  id: '',
  name: '',
  description: '',
  capability: 'classify' as AICapability,
  provider_id: '',
  model: '',
  temperature: 0,
  max_output_tokens: 160,
  response_format: 'auto',
  timeout_seconds: 20,
  output_language: 'auto' as AIOutputLanguage,
  reasoning_effort: 'provider_default' as AIReasoningEffort,
  verbosity: 'standard' as AIVerbosity,
  include_reason: true,
  max_reason_characters: 200,
  system_instructions: '',
  cache_ttl_seconds: 2592000,
  daily_request_limit: 500 as number | '',
  daily_token_limit: 1000000 as number | '',
  enabled: true,
})
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
    if (!allowedModels.value.some((model) => model.model_id === form.model)) form.model = ''
  } catch (error) {
    if (form.provider_id !== requestedProviderId) return
    form.model = ''
    ui.toast(error instanceof Error ? error.message : 'Provider 模型加载失败', 'danger')
  } finally {
    if (form.provider_id === requestedProviderId) modelsLoading.value = false
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
    if (!form.provider_id && providers.value[0]) form.provider_id = providers.value[0].id
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : 'AI Profile 加载失败', 'danger')
  }
}

function resetForm() {
  Object.assign(form, {
    id: '',
    name: '',
    description: '',
    capability: 'classify' as AICapability,
    provider_id: providers.value[0]?.id ?? '',
    model: '',
    temperature: 0,
    max_output_tokens: 160,
    response_format: 'auto',
    timeout_seconds: 20,
    output_language: 'auto' as AIOutputLanguage,
    reasoning_effort: 'provider_default' as AIReasoningEffort,
    verbosity: 'standard' as AIVerbosity,
    include_reason: true,
    max_reason_characters: 200,
    system_instructions: '',
    cache_ttl_seconds: 2592000,
    daily_request_limit: 500,
    daily_token_limit: 1000000,
    enabled: true,
  })
}

function openCreate() {
  editingId.value = undefined
  resetForm()
  show.value = true
  void loadProviderModels(form.provider_id)
}

function openEdit(item: AIProfile) {
  editingId.value = item.id
  Object.assign(form, {
    id: item.id,
    name: item.name,
    description: item.description,
    capability: item.capability,
    provider_id: item.provider_id,
    model: item.model,
    temperature: item.temperature,
    max_output_tokens: item.max_output_tokens,
    response_format: item.response_format,
    timeout_seconds: item.timeout_seconds,
    output_language: item.output_language,
    reasoning_effort: item.reasoning_effort,
    verbosity: item.verbosity,
    include_reason: item.include_reason,
    max_reason_characters: item.max_reason_characters,
    system_instructions: item.system_instructions,
    cache_ttl_seconds: item.cache_ttl_seconds,
    daily_request_limit: item.daily_request_limit ?? '',
    daily_token_limit: item.daily_token_limit ?? '',
    enabled: item.enabled,
  })
  show.value = true
  void loadProviderModels(item.provider_id)
}

function closeEditor() {
  show.value = false
  editingId.value = undefined
}

function policyPayload() {
  return {
    name: form.name,
    description: form.description,
    provider_id: form.provider_id,
    model: form.model,
    temperature: form.temperature,
    max_output_tokens: form.max_output_tokens,
    response_format: form.response_format,
    timeout_seconds: form.timeout_seconds,
    output_language: form.output_language,
    reasoning_effort: form.reasoning_effort,
    verbosity: form.verbosity,
    include_reason: form.include_reason,
    max_reason_characters: form.max_reason_characters,
    system_instructions: form.system_instructions,
    cache_ttl_seconds: form.cache_ttl_seconds,
    daily_request_limit: form.daily_request_limit === '' ? null : form.daily_request_limit,
    daily_token_limit: form.daily_token_limit === '' ? null : form.daily_token_limit,
    enabled: form.enabled,
  }
}

async function saveProfile() {
  busy.value = true
  try {
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
    closeEditor()
    await load()
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : editingId.value ? '保存失败' : '创建失败', 'danger')
  } finally {
    busy.value = false
  }
}

async function deleteProfile() {
  if (!deleteTarget.value) return
  deleteBusy.value = true
  try {
    await api.delete(`/admin/ai/profiles/${deleteTarget.value.id}`)
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
  } finally {
    deleteBusy.value = false
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
    <button class="btn btn--primary" :disabled="!providers.length" @click="openCreate">
      新增 Profile
    </button>
  </PageHeader>

  <div v-if="!providers.length" class="warning-box profile-warning">
    请先创建 AI Provider，同步远端模型并明确授权可用模型。
  </div>

  <section v-if="show" class="panel profile-builder">
    <form @submit.prevent="saveProfile">
      <div class="profile-builder__intro">
        <div>
          <p class="eyebrow">
            RUNTIME POLICY
          </p>
          <h2>{{ editingId ? '编辑模型运行方案' : '创建模型运行方案' }}</h2>
        </div>
        <p>Profile 决定“怎么调用”；插件决定“调用来做什么”。平台安全约束和结构化校验始终生效。</p>
      </div>

      <fieldset class="policy-section">
        <legend><span>01</span>身份与能力</legend>
        <div class="policy-grid">
          <div class="field">
            <label for="profile-id">稳定 ID{{ editingId ? '' : '（可选）' }}</label>
            <input id="profile-id" v-model="form.id" class="input mono" :disabled="Boolean(editingId)" placeholder="semantic_classifier_fast">
            <small>{{ editingId ? '插件授权与调用通过此 ID 绑定，编辑时不可更改。' : '插件通过这个 ID 引用 Profile；创建后不可更改。' }}</small>
          </div>
          <div class="field">
            <label for="profile-name">名称</label>
            <input id="profile-name" v-model="form.name" class="input" required placeholder="快速语义分类">
          </div>
          <div class="field field--wide">
            <label for="profile-description">用途说明</label>
            <textarea id="profile-description" v-model="form.description" class="input textarea" rows="2" placeholder="用于低延迟、低成本的文本分类任务。" />
          </div>
          <div class="field">
            <label for="profile-capability">能力类型</label>
            <select id="profile-capability" v-model="form.capability" class="select" :disabled="Boolean(editingId)">
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
            <small>{{ editingId ? '能力是插件调用契约的一部分；如需变更，请创建新 Profile。' : '决定插件可使用的 Gateway 方法和结构化输出协议。' }}</small>
          </div>
          <label class="check-card">
            <input v-model="form.enabled" type="checkbox">
            <span><strong>{{ editingId ? '启用此 Profile' : '创建后立即启用' }}</strong><small>停用的 Profile 不接受新的模型调用。</small></span>
          </label>
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
            <input id="profile-temperature" v-model.number="form.temperature" class="input" type="number" min="0" max="2" step="0.1">
          </div>
          <div class="field">
            <label for="profile-max-output">最大输出 Token</label>
            <input id="profile-max-output" v-model.number="form.max_output_tokens" class="input" type="number" min="1" max="100000">
          </div>
          <div class="field">
            <label for="profile-timeout">调用超时（秒）</label>
            <input id="profile-timeout" v-model.number="form.timeout_seconds" class="input" type="number" min="1" max="300">
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
          <label class="check-card">
            <input v-model="form.include_reason" type="checkbox">
            <span><strong>返回判断理由</strong><small>让插件获得简短、可审计的 reason 字段。</small></span>
          </label>
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
          <div class="field field--wide field--full">
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
            <input id="profile-cache" v-model.number="form.cache_ttl_seconds" class="input" type="number" min="0" max="31536000">
            <small>设置为 0 可关闭持久化结果缓存。</small>
          </div>
          <div class="field">
            <label for="profile-request-limit">每日请求上限</label>
            <input id="profile-request-limit" v-model.number="form.daily_request_limit" class="input" type="number" min="1" max="1000000">
            <small>留空表示无限制。</small>
          </div>
          <div class="field">
            <label for="profile-token-limit">每日 Token 上限</label>
            <input id="profile-token-limit" v-model.number="form.daily_token_limit" class="input" type="number" min="1" max="1000000000">
            <small>留空表示无限制。</small>
          </div>
        </div>
      </fieldset>

      <div class="profile-builder__footer">
        <span class="muted">{{ editingId ? '保存后新调用立即使用更新后的策略；稳定 ID、能力与历史调用记录保持不变。' : '创建后插件只需要引用稳定 Profile ID，不会接触 Provider 地址、API Key 或模型参数。' }}</span>
        <div>
          <button type="button" class="btn btn--ghost" :disabled="busy" @click="closeEditor">
            取消
          </button>
          <button class="btn btn--primary" :disabled="busy || modelsLoading || !form.model">
            {{ busy ? (editingId ? '正在保存…' : '正在创建…') : (editingId ? '保存更改' : '创建 Profile') }}
          </button>
        </div>
      </div>
    </form>
  </section>

  <EmptyState v-if="!items.length" />
  <div v-else class="table-wrap profile-table">
    <table>
      <thead>
        <tr><th>Profile</th><th>能力</th><th>模型路由</th><th>输出策略</th><th>成本策略</th><th>状态</th><th>操作</th></tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.id">
          <td>
            <strong>{{ item.name }}</strong><br>
            <span class="mono muted">{{ item.id }}</span>
            <small v-if="item.description" class="table-description">{{ item.description }}</small>
          </td>
          <td><span class="policy-tag">{{ capabilityLabels[item.capability] }}</span></td>
          <td>
            {{ providerName(item.provider_id) }}<br>
            <span class="mono muted">{{ item.model }}</span>
          </td>
          <td>
            {{ languageLabels[item.output_language] }} · {{ verbosityLabels[item.verbosity] }}<br>
            <span class="muted">推理 {{ reasoningLabels[item.reasoning_effort] }} · {{ item.include_reason ? `理由 ≤ ${item.max_reason_characters} 字` : '无理由' }}</span>
          </td>
          <td>
            {{ item.daily_request_limit ?? '无限制' }} 次 / 天<br>
            <span class="muted">{{ item.daily_token_limit ?? '无限制' }} Token · 缓存 {{ days(item.cache_ttl_seconds) }}</span>
          </td>
          <td><StatusBadge :status="item.enabled ? 'active' : 'disabled'" /><br><span class="muted">r{{ item.revision }}</span></td>
          <td>
            <button class="btn btn--ghost btn--small" @click="openEdit(item)">
              编辑
            </button>
            <button class="btn btn--danger btn--small" @click="deleteTarget = item">
              删除
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>

  <section class="panel invocation-panel">
    <div class="panel-title">
      <h2>最近调用</h2><span class="muted">删除 Profile 后仍保留历史记录；不保存正文、Prompt 或凭据</span>
    </div>
    <div v-if="invocations.length" class="table-wrap">
      <table>
        <thead><tr><th>时间</th><th>Profile</th><th>插件 / 用途</th><th>缓存</th><th>Token</th><th>延迟</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="invocation in invocations" :key="invocation.id">
            <td>{{ new Date(invocation.created_at).toLocaleString() }}</td>
            <td class="mono">
              {{ invocation.profile_id }}
            </td>
            <td>{{ invocation.plugin_id ?? 'platform' }} · {{ invocation.use_case }}</td>
            <td>{{ invocation.cache_hit ? 'hit' : 'miss' }}</td>
            <td>{{ (invocation.input_tokens ?? 0) + (invocation.output_tokens ?? 0) }}</td>
            <td>{{ invocation.latency_ms ?? 0 }} ms</td>
            <td><StatusBadge :status="invocation.status" /></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="muted">
      暂无调用记录。
    </div>
  </section>

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
.profile-warning,.profile-builder{margin-bottom:16px}
.profile-builder{padding:0;border-top:3px solid var(--accent);overflow:hidden}
.profile-builder__intro,.profile-builder__footer{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;padding:20px 22px;background:#efeee8;border-bottom:1px solid var(--line)}
.profile-builder__intro h2{margin:0;font-size:19px}
.profile-builder__intro p:last-child{max-width:620px;margin:0;color:var(--muted);font-size:12px;line-height:1.7}
.policy-section{margin:0;padding:21px 22px 24px;border:0;border-bottom:1px solid var(--line)}
.policy-section legend{width:100%;padding:0 0 14px;font-weight:700;font-size:14px}
.policy-section legend span{display:inline-grid;place-items:center;width:26px;height:20px;margin-right:8px;background:var(--ink);color:white;font:10px var(--mono)}
.policy-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px 18px}
.policy-grid--three{grid-template-columns:repeat(3,minmax(0,1fr))}
.field--wide{grid-column:span 1}.field--span-two{grid-column:span 2}.field--full{grid-column:1/-1}
.field small{display:block;margin-top:5px;color:var(--muted);font-size:10px;line-height:1.5}
.field small.danger{color:var(--red)}
.textarea{height:auto;min-height:unset;resize:vertical;line-height:1.6}
.check-card{display:flex;align-items:flex-start;gap:10px;padding:11px 12px;border:1px solid var(--line);background:#f7f6f1;cursor:pointer}
.check-card input{margin-top:3px}.check-card span{display:grid;gap:3px}.check-card strong{font-size:12px}.check-card small{color:var(--muted);font-size:10px;line-height:1.5}
.constraint-note{margin-top:7px;padding:9px 11px;border-left:3px solid var(--accent);background:#fff6ee;color:var(--muted);font-size:11px;line-height:1.6}
.constraint-note strong{color:var(--ink)}
.profile-builder__footer{align-items:center;border:0;background:#f7f6f1;font-size:11px}
.profile-builder__footer>div{display:flex;gap:8px;flex:none}
.profile-table{overflow-x:auto}.profile-table table{min-width:1120px}.profile-table td{vertical-align:top}
.table-description{display:block;max-width:240px;margin-top:7px;color:var(--muted);font-size:10px;line-height:1.5}
.policy-tag{display:inline-block;padding:4px 7px;border:1px solid var(--line);background:#efeee8;font:10px var(--mono)}
.invocation-panel{margin-top:20px}
@media(max-width:1000px){.policy-grid--three{grid-template-columns:repeat(2,minmax(0,1fr))}.field--span-two{grid-column:span 1}}
@media(max-width:760px){.profile-builder__intro,.profile-builder__footer{align-items:stretch;flex-direction:column}.profile-builder__footer>div{justify-content:flex-end}.policy-grid,.policy-grid--three{grid-template-columns:1fr}.field--wide,.field--span-two,.field--full{grid-column:auto}.policy-section{padding-inline:16px}}
</style>
