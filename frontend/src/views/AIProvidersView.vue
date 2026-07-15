<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { api, ApiError } from '@/lib/api'
import type { AIProvider, AIProviderModel } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppSelect from '@/components/ui/AppSelect.vue'
import AppCheckbox from '@/components/ui/AppCheckbox.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import DataTable from '@/components/data/DataTable.vue'
import { useUiStore } from '@/stores/ui'

const presetUrls: Record<string, string> = {
  openai: 'https://api.openai.com/v1',
  gemini: 'https://generativelanguage.googleapis.com/v1beta/openai',
  openrouter: 'https://openrouter.ai/api/v1',
  deepseek: 'https://api.deepseek.com',
  kimi: 'https://api.moonshot.cn/v1',
  dashscope: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  zhipu: 'https://open.bigmodel.cn/api/paas/v4',
  siliconflow: 'https://api.siliconflow.cn/v1',
}
const ui = useUiStore()
const items = ref<AIProvider[]>([])
const show = ref(false)
const busy = ref(false)
const editingId = ref<string>()
const deleteTarget = ref<AIProvider>()
const deleteBusy = ref(false)
const keyTarget = ref<string>()
const modelTarget = ref<string>()
const models = ref<AIProviderModel[]>([])
const selectedModelIds = ref<string[]>([])
const modelsLoading = ref(false)
const modelsSyncing = ref(false)
const modelsSaving = ref(false)

const form = reactive({
  name: '',
  preset: 'custom',
  base_url: 'https://api.example.com/v1',
  api_key: '',
  allow_private_network: false,
  enabled: true,
  timeout_seconds: 30,
  max_retries: 2,
  verify_tls: true,
  structured_output_mode: 'auto',
})

const keyForm = reactive({ value: '' })
const modelProvider = computed(() => items.value.find((item) => item.id === modelTarget.value))
const availableCount = computed(() => models.value.filter((model) => model.available).length)

watch(
  () => form.preset,
  (preset) => {
    if (presetUrls[preset]) {
      form.base_url = presetUrls[preset]
    }
  },
)

function applyModels(result: { models: AIProviderModel[] }) {
  models.value = result.models
  selectedModelIds.value = result.models
    .filter((model) => model.available && model.enabled)
    .map((model) => model.model_id)
}

async function load() {
  try {
    items.value = await api.get<AIProvider[]>('/admin/ai/providers')
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : 'AI Provider 加载失败', 'danger')
  }
}

function resetForm() {
  editingId.value = undefined
  Object.assign(form, {
    name: '',
    preset: 'custom',
    base_url: 'https://api.example.com/v1',
    api_key: '',
    allow_private_network: false,
    enabled: true,
    timeout_seconds: 30,
    max_retries: 2,
    verify_tls: true,
    structured_output_mode: 'auto',
  })
}

function closeForm() {
  show.value = false
  resetForm()
}

function openCreate() {
  if (show.value && !editingId.value) {
    closeForm()
    return
  }
  resetForm()
  show.value = true
}

async function startEdit(provider: AIProvider) {
  editingId.value = provider.id
  show.value = true
  form.preset = provider.preset
  await nextTick()
  Object.assign(form, {
    name: provider.name,
    base_url: provider.base_url,
    api_key: '',
    allow_private_network: provider.allow_private_network,
    enabled: provider.enabled,
    timeout_seconds: provider.timeout_seconds,
    max_retries: provider.max_retries,
    verify_tls: provider.verify_tls,
    structured_output_mode: provider.structured_output_mode,
  })
}

async function saveProvider() {
  busy.value = true
  try {
    const payload = {
      name: form.name,
      preset: form.preset,
      base_url: form.base_url,
      allow_private_network: form.allow_private_network,
      enabled: form.enabled,
      timeout_seconds: form.timeout_seconds,
      max_retries: form.max_retries,
      verify_tls: form.verify_tls,
      structured_output_mode: form.structured_output_mode,
    }
    if (editingId.value) {
      await api.patch(`/admin/ai/providers/${editingId.value}`, payload)
      ui.toast('Provider 配置已更新', 'success')
    } else {
      await api.post('/admin/ai/providers', {
        ...payload,
        api_key: form.api_key || undefined,
        protocol: 'openai_chat_completions',
      })
      ui.toast('Provider 与凭据已安全保存', 'success')
    }
    closeForm()
    await load()
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : '创建失败', 'danger')
  } finally {
    busy.value = false
  }
}

async function deleteProvider() {
  if (!deleteTarget.value) return
  deleteBusy.value = true
  try {
    const providerId = deleteTarget.value.id
    await api.delete(`/admin/ai/providers/${providerId}`)
    if (keyTarget.value === providerId) keyTarget.value = undefined
    if (modelTarget.value === providerId) closeModels()
    if (editingId.value === providerId) closeForm()
    deleteTarget.value = undefined
    ui.toast('Provider 已删除，历史调用记录继续保留', 'success')
    await load()
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      ui.toast('该 Provider 仍被 AI Profile 引用，请先迁移或删除相关 Profile', 'danger')
    } else {
      ui.toast(error instanceof Error ? error.message : '删除失败', 'danger')
    }
  } finally {
    deleteBusy.value = false
  }
}

async function saveKey() {
  if (!keyTarget.value) return
  busy.value = true
  try {
    await api.put(`/admin/ai/providers/${keyTarget.value}/api-key`, keyForm)
    keyForm.value = ''
    keyTarget.value = undefined
    ui.toast('API Key 已安全保存', 'success')
    await load()
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : '保存失败', 'danger')
  } finally {
    busy.value = false
  }
}

async function testProvider(id: string) {
  try {
    const result = await api.post<{ model_count: number }>(`/admin/ai/providers/${id}/test`)
    ui.toast(`Provider 可用，远端返回 ${result.model_count} 个模型`, 'success')
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : '连接测试失败', 'danger')
  }
}

async function configureModels(provider: AIProvider) {
  modelTarget.value = provider.id
  models.value = []
  selectedModelIds.value = []
  modelsLoading.value = true
  try {
    applyModels(
      await api.get<{ models: AIProviderModel[] }>(`/admin/ai/providers/${provider.id}/models`),
    )
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : '模型清单加载失败', 'danger')
  } finally {
    modelsLoading.value = false
  }
}

async function syncModels() {
  if (!modelTarget.value) return
  modelsSyncing.value = true
  try {
    applyModels(
      await api.post<{ models: AIProviderModel[] }>(
        `/admin/ai/providers/${modelTarget.value}/models/sync`,
      ),
    )
    ui.toast(`同步完成，发现 ${availableCount.value} 个当前可用模型`, 'success')
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : '模型同步失败', 'danger')
  } finally {
    modelsSyncing.value = false
  }
}

async function saveAllowedModels() {
  if (!modelTarget.value) return
  modelsSaving.value = true
  try {
    applyModels(
      await api.put<{ models: AIProviderModel[] }>(
        `/admin/ai/providers/${modelTarget.value}/models/allowed`,
        { model_ids: selectedModelIds.value },
      ),
    )
    ui.toast('可用模型范围已保存', 'success')
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : '可用模型保存失败', 'danger')
  } finally {
    modelsSaving.value = false
  }
}

function closeModels() {
  modelTarget.value = undefined
  models.value = []
  selectedModelIds.value = []
}

onMounted(load)
</script>

<template>
  <PageHeader title="AI Providers" description="平台统一维护端点与凭据；插件无法读取 API 地址、Key 或模型。">
    <AppButton variant="primary" @click="openCreate">
      {{ show && !editingId ? '收起' : '新增 Provider' }}
    </AppButton>
  </PageHeader>

  <AppCard v-if="show" padding="md" class="create-card">
    <template #header>
      <div class="provider-form__heading">
        <div>
          <p class="eyebrow">
            {{ editingId ? 'EDIT PROVIDER' : 'NEW PROVIDER' }}
          </p>
          <h3 class="panel-title">
            {{ editingId ? '编辑 Provider' : '新增 Provider' }}
          </h3>
        </div>
        <AppButton size="sm" @click="closeForm">
          取消
        </AppButton>
      </div>
    </template>
    <form class="grid form-grid" @submit.prevent="saveProvider">
      <div class="field">
        <label>名称</label>
        <AppInput v-model="form.name" required />
      </div>
      
      <div class="field">
        <label>预置</label>
        <AppSelect v-model="form.preset">
          <option value="custom">
            自定义 OpenAI 兼容
          </option>
          <option value="openai">
            OpenAI
          </option>
          <option value="azure_openai">
            Azure OpenAI（填写资源地址）
          </option>
          <option value="deepseek">
            DeepSeek
          </option>
          <option value="openrouter">
            OpenRouter
          </option>
          <option value="gemini">
            Gemini
          </option>
          <option value="kimi">
            Kimi
          </option>
          <option value="zhipu">
            智谱 GLM
          </option>
          <option value="siliconflow">
            SiliconFlow
          </option>
          <option value="dashscope">
            阿里云百炼（填写地域 / Workspace 地址）
          </option>
        </AppSelect>
      </div>

      <div class="field">
        <label>Base URL</label>
        <AppInput v-model="form.base_url" required />
      </div>

      <div v-if="!editingId" class="field">
        <label>API Key（可选，保存后不再回显）</label>
        <AppInput v-model="form.api_key" type="password" autocomplete="new-password" placeholder="sk-..." />
      </div>
      <div v-else class="field provider-form__key-note">
        <label>API Key</label>
        <span class="muted">凭据不会回显；如需更换，请使用列表中的“设置 Key”。</span>
      </div>

      <div class="field">
        <label>请求超时（秒）</label>
        <AppInput v-model.number="form.timeout_seconds" type="number" min="1" max="300" required />
      </div>

      <div class="field">
        <label>最大重试次数</label>
        <AppInput v-model.number="form.max_retries" type="number" min="0" max="5" required />
      </div>

      <div class="field">
        <label>结构化输出模式</label>
        <AppSelect v-model="form.structured_output_mode">
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
            Prompt JSON
          </option>
        </AppSelect>
      </div>

      <div class="provider-form__checks full-width">
        <AppCheckbox v-model="form.enabled">
          启用 Provider
        </AppCheckbox>
        <AppCheckbox v-model="form.allow_private_network">
          允许私网端点（高风险）
        </AppCheckbox>
        <AppCheckbox v-model="form.verify_tls">
          校验 TLS 证书
        </AppCheckbox>
      </div>

      <div class="form-submit-row full-width">
        <AppButton type="submit" variant="primary" :loading="busy">
          {{ busy ? '正在保存…' : editingId ? '保存修改' : '创建并安全保存' }}
        </AppButton>
      </div>
    </form>
  </AppCard>

  <AppCard v-if="keyTarget" padding="md" class="key-card">
    <template #header>
      <h3 class="panel-title">
        更换 API Key
      </h3>
    </template>
    <form class="key-form" @submit.prevent="saveKey">
      <div class="field flex-1">
        <label>API Key（保存后不再回显）</label>
        <AppInput v-model="keyForm.value" type="password" autocomplete="new-password" required />
      </div>
      <div class="form-actions">
        <AppButton type="submit" variant="primary" :loading="busy">
          安全保存
        </AppButton>
        <AppButton @click="keyTarget=undefined; keyForm.value=''">
          取消
        </AppButton>
      </div>
    </form>
  </AppCard>

  <AppCard v-if="modelTarget" padding="md" class="model-control">
    <template #header>
      <div class="panel-header-wrap">
        <div>
          <p class="eyebrow">
            MODEL ACCESS
          </p>
          <h3 class="panel-title">
            {{ modelProvider?.name ?? modelTarget }}
          </h3>
          <p class="model-control__description">
            同步只负责发现远端模型；新发现的模型默认禁用。仅勾选并保存的模型可用于创建 AI Profile。
          </p>
        </div>
        <div class="model-control__actions">
          <AppButton size="sm" :loading="modelsSyncing" @click="syncModels">
            {{ modelsSyncing ? '正在同步…' : '从远端同步' }}
          </AppButton>
          <AppButton size="sm" @click="closeModels">
            关闭
          </AppButton>
        </div>
      </div>
    </template>

    <div v-if="modelsLoading" class="loading">
      正在读取模型清单…
    </div>
    
    <AppAlert v-else-if="!models.length" variant="warning">
      尚未发现模型。请先确认 Provider 凭据和连接测试正常，然后点击“从远端同步”。
    </AppAlert>

    <template v-else>
      <div class="model-summary" aria-live="polite">
        <span><strong>{{ models.length }}</strong> 个已发现</span>
        <span><strong>{{ availableCount }}</strong> 个远端可用</span>
        <span><strong>{{ selectedModelIds.length }}</strong> 个已选择</span>
      </div>

      <fieldset class="model-list">
        <legend class="sr-only">
          允许 AI Profile 使用的模型
        </legend>
        <label
          v-for="model in models"
          :key="model.id"
          class="model-option"
          :class="{ 'model-option--unavailable': !model.available }"
        >
          <input
            v-model="selectedModelIds"
            type="checkbox"
            :value="model.model_id"
            :disabled="!model.available"
          >
          <span class="model-option__id mono">{{ model.model_id }}</span>
          <span v-if="model.available" class="model-option__state">
            {{ model.enabled ? '已允许' : '待授权' }}
          </span>
          <span v-else class="model-option__state text-danger">远端已不可用</span>
        </label>
      </fieldset>

      <div class="model-control__footer">
        <span class="muted">取消勾选后，新 Profile 无法选择该模型，引用它的现有 Profile 也会停止调用。</span>
        <AppButton
          variant="primary"
          :loading="modelsSaving"
          @click="saveAllowedModels"
        >
          保存允许范围
        </AppButton>
      </div>
    </template>
  </AppCard>

  <EmptyState v-if="!items.length" />

  <AppCard v-else padding="none" class="provider-table-card">
    <div class="table-wrap provider-table">
      <DataTable>
        <template #headers>
          <th>名称</th>
          <th>预置</th>
          <th>端点</th>
          <th>凭据</th>
          <th>状态</th>
          <th>操作</th>
        </template>
        <tr v-for="item in items" :key="item.id">
          <td>
            <div class="provider-cell">
              <strong>{{ item.name }}</strong>
              <span class="mono muted item-id">{{ item.id }}</span>
            </div>
          </td>
          <td>{{ item.preset }}</td>
          <td>
            <span class="mono text-url">{{ item.base_url }}</span>
          </td>
          <td>{{ item.api_key_configured ? '已配置' : '未配置' }}</td>
          <td>
            <StatusBadge :status="item.enabled ? 'active' : 'disabled'" />
          </td>
          <td>
            <div class="provider-actions">
              <AppButton size="sm" @click="testProvider(item.id)">
                连接测试
              </AppButton>
              <AppButton size="sm" @click="configureModels(item)">
                同步 / 配置模型
              </AppButton>
              <AppButton size="sm" @click="keyTarget=item.id">
                设置 Key
              </AppButton>
              <AppButton size="sm" @click="startEdit(item)">
                编辑
              </AppButton>
              <AppButton variant="danger" size="sm" class="btn--danger" @click="deleteTarget=item">
                删除
              </AppButton>
            </div>
          </td>
        </tr>
      </DataTable>
    </div>
  </AppCard>

  <ConfirmDialog
    :open="Boolean(deleteTarget)"
    title="删除 AI Provider？"
    :description="`将停用并移除 ${deleteTarget?.name ?? '该 Provider'}，同时删除其凭据；历史模型配置和调用记录继续保留。若仍有 AI Profile 引用，系统会阻止删除。`"
    confirm-text="确认删除"
    danger
    :busy="deleteBusy"
    @confirm="deleteProvider"
    @cancel="deleteTarget=undefined"
  />
</template>

<style scoped>
.create-card, .key-card, .model-control {
  margin-bottom: var(--space-4);
}

.panel-header-wrap {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  width: 100%;
}

.provider-form__heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  width: 100%;
}

.panel-title {
  font-size: var(--text-md);
  font-weight: 700;
  margin: 0;
}

.form-grid {
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

.provider-form__checks {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-2) 0;
}

.provider-form__key-note {
  justify-content: center;
}

.provider-form__key-note span {
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

.form-submit-row {
  margin-top: var(--space-2);
}

.key-form {
  display: flex;
  align-items: flex-end;
  gap: var(--space-4);
}

.form-actions {
  display: flex;
  gap: var(--space-2);
}

.model-control {
  border-top: 3px solid var(--action-primary);
}

.model-control__description {
  max-width: 720px;
  margin: var(--space-1) 0 0 0;
  color: var(--text-secondary);
  font-size: var(--text-xs);
  line-height: var(--leading-normal);
}

.model-control__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.model-summary {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-5);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--border-default);
  background-color: var(--color-neutral-100);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

.model-summary strong {
  font-size: var(--text-md);
  color: var(--text-primary);
}

.model-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 0;
  margin: var(--space-4) 0 0;
  padding: 0;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.model-option {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--border-default);
  background-color: var(--surface-panel);
  font-size: var(--text-sm);
  cursor: pointer;
}

.model-option:last-child {
  border-bottom: none;
}

.model-option:hover {
  background-color: var(--surface-hover);
}

.model-option--unavailable {
  background-color: var(--color-neutral-100);
  color: var(--text-secondary);
  cursor: not-allowed;
}

.model-option__id {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-option__state {
  color: var(--text-secondary);
  font-size: var(--text-xs);
}

.model-option__state.text-danger {
  color: var(--status-danger);
}

.model-control__footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: var(--space-4);
  font-size: var(--text-xs);
  gap: var(--space-4);
}

.provider-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.item-id {
  font-size: 11px;
}

.text-url {
  max-width: 250px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: inline-block;
  vertical-align: middle;
}

.provider-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@media (max-width: 760px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
  
  .key-form {
    flex-direction: column;
    align-items: stretch;
  }
  
  .model-control__header {
    flex-direction: column;
    gap: var(--space-3);
  }
  
  .model-control__footer {
    flex-direction: column;
    align-items: stretch;
  }
  
  .model-list {
    grid-template-columns: 1fr;
  }
}
</style>
