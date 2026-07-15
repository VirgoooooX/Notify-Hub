<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { api, ApiError } from '@/lib/api'
import type { AIProvider, AIProviderModel } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
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
    if (presetUrls[preset]) form.base_url = presetUrls[preset]
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
    <button class="btn btn--primary" @click="openCreate">
      {{ show && !editingId ? '收起' : '新增 Provider' }}
    </button>
  </PageHeader>
  <section v-if="show" class="panel" style="margin-bottom:16px">
    <div class="provider-form__heading">
      <div>
        <p class="eyebrow">
          {{ editingId ? 'EDIT PROVIDER' : 'NEW PROVIDER' }}
        </p>
        <h2>{{ editingId ? '编辑 Provider' : '新增 Provider' }}</h2>
      </div>
      <button type="button" class="btn btn--ghost" @click="closeForm">
        取消
      </button>
    </div>
    <form class="grid split" @submit.prevent="saveProvider">
      <div class="field">
        <label>名称</label><input v-model="form.name" class="input" required>
      </div><div class="field">
        <label>预置</label><select v-model="form.preset" class="select">
          <option value="custom">
            自定义 OpenAI 兼容
          </option><option value="openai">
            OpenAI
          </option><option value="azure_openai">
            Azure OpenAI（填写资源地址）
          </option><option value="deepseek">
            DeepSeek
          </option><option value="openrouter">
            OpenRouter
          </option><option value="gemini">
            Gemini
          </option><option value="kimi">
            Kimi
          </option><option value="zhipu">
            智谱 GLM
          </option><option value="siliconflow">
            SiliconFlow
          </option><option value="dashscope">
            阿里云百炼（填写地域 / Workspace 地址）
          </option>
        </select>
      </div><div class="field">
        <label>Base URL</label><input v-model="form.base_url" class="input" required>
      </div><div v-if="!editingId" class="field">
        <label>API Key（可选，保存后不再回显）</label><input v-model="form.api_key" class="input" type="password" autocomplete="new-password" placeholder="sk-...">
      </div><div v-else class="field provider-form__key-note">
        <label>API Key</label><span>凭据不会回显；如需更换，请使用列表中的“设置 Key”。</span>
      </div><div class="field">
        <label>请求超时（秒）</label><input v-model.number="form.timeout_seconds" class="input" type="number" min="1" max="300" required>
      </div><div class="field">
        <label>最大重试次数</label><input v-model.number="form.max_retries" class="input" type="number" min="0" max="5" required>
      </div><div class="field">
        <label>结构化输出模式</label><select v-model="form.structured_output_mode" class="select">
          <option value="auto">
            自动协商
          </option><option value="json_schema">
            JSON Schema
          </option><option value="json_object">
            JSON Object
          </option><option value="prompt_json">
            Prompt JSON
          </option>
        </select>
      </div><div class="provider-form__checks">
        <label><input v-model="form.enabled" type="checkbox"> 启用 Provider</label>
        <label><input v-model="form.allow_private_network" type="checkbox"> 允许私网端点（高风险）</label>
        <label><input v-model="form.verify_tls" type="checkbox"> 校验 TLS 证书</label>
      </div><button class="btn btn--primary" :disabled="busy">
        {{ busy ? '正在保存…' : editingId ? '保存修改' : '创建并安全保存' }}
      </button>
    </form>
  </section>
  <section v-if="keyTarget" class="panel" style="margin-bottom:16px">
    <form class="filters" @submit.prevent="saveKey">
      <div class="field">
        <label>API Key（保存后不再回显）</label><input v-model="keyForm.value" class="input" type="password" autocomplete="new-password" required>
      </div><button class="btn btn--primary" :disabled="busy">
        安全保存
      </button><button type="button" class="btn btn--ghost" @click="keyTarget=undefined;keyForm.value=''">
        取消
      </button>
    </form>
  </section>
  <section v-if="modelTarget" class="panel model-control" style="margin-bottom:16px">
    <div class="panel-title model-control__header">
      <div>
        <p class="eyebrow">
          MODEL ACCESS
        </p>
        <h2>{{ modelProvider?.name ?? modelTarget }}</h2>
        <p class="model-control__description">
          同步只负责发现远端模型；新发现的模型默认禁用。仅勾选并保存的模型可用于创建 AI Profile。
        </p>
      </div>
      <div class="model-control__actions">
        <button type="button" class="btn btn--ghost" :disabled="modelsSyncing" @click="syncModels">
          {{ modelsSyncing ? '正在同步…' : '从远端同步' }}
        </button>
        <button type="button" class="btn btn--ghost" @click="closeModels">
          关闭
        </button>
      </div>
    </div>
    <div v-if="modelsLoading" class="loading">
      正在读取模型清单…
    </div>
    <div v-else-if="!models.length" class="warning-box">
      尚未发现模型。请先确认 Provider 凭据和连接测试正常，然后点击“从远端同步”。
    </div>
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
          <span v-else class="model-option__state danger">远端已不可用</span>
        </label>
      </fieldset>
      <div class="model-control__footer">
        <span class="muted">取消勾选后，新 Profile 无法选择该模型，引用它的现有 Profile 也会停止调用。</span>
        <button
          type="button"
          class="btn btn--primary"
          :disabled="modelsSaving"
          @click="saveAllowedModels"
        >
          {{ modelsSaving ? '正在保存…' : '保存允许范围' }}
        </button>
      </div>
    </template>
  </section>
  <EmptyState v-if="!items.length" /><div v-else class="table-wrap provider-table">
    <table>
      <thead><tr><th>名称</th><th>预置</th><th>端点</th><th>凭据</th><th>状态</th><th>操作</th></tr></thead><tbody>
        <tr v-for="item in items" :key="item.id">
          <td><strong>{{ item.name }}</strong><br><span class="mono muted">{{ item.id }}</span></td><td>{{ item.preset }}</td><td class="mono">
            {{ item.base_url }}
          </td><td>{{ item.api_key_configured?'已配置':'未配置' }}</td><td><StatusBadge :status="item.enabled?'active':'disabled'" /></td><td>
            <div class="provider-actions">
              <button class="btn btn--ghost btn--small" @click="testProvider(item.id)">
                连接测试
              </button>
              <button class="btn btn--ghost btn--small" @click="configureModels(item)">
                同步 / 配置模型
              </button>
              <button class="btn btn--ghost btn--small" @click="keyTarget=item.id">
                设置 Key
              </button>
              <button class="btn btn--ghost btn--small" @click="startEdit(item)">
                编辑
              </button>
              <button class="btn btn--danger btn--small" @click="deleteTarget=item">
                删除
              </button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
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
.model-control{border-top:3px solid var(--accent)}
.provider-form__heading{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:16px}.provider-form__heading h2{margin:0;font-size:18px}.provider-form__checks{display:flex;flex-wrap:wrap;align-items:center;gap:18px}.provider-form__key-note span{min-height:40px;display:flex;align-items:center;color:var(--muted);font-size:12px}.provider-table table{min-width:1120px}.provider-actions{display:flex;flex-wrap:wrap;gap:6px}
.model-control__header{align-items:flex-start;gap:24px}
.model-control__header h2{margin:0;font-size:18px}
.model-control__description{max-width:720px;margin:7px 0 0;color:var(--muted);font-size:12px;line-height:1.7}
.model-control__actions,.model-control__footer{display:flex;align-items:center;justify-content:flex-end;gap:8px}
.model-summary{display:flex;flex-wrap:wrap;gap:20px;padding:12px 14px;border:1px solid var(--line);background:#efeee8;font:11px var(--mono)}
.model-summary strong{font-size:16px;color:var(--ink)}
.model-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:0;margin:14px 0 0;padding:0;border:1px solid var(--line)}
.model-option{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:10px;padding:12px 14px;border-bottom:1px solid var(--line);background:var(--paper);font-size:12px}
.model-option:hover{background:#fffdf6}
.model-option--unavailable{background:#f0efea;color:var(--muted)}
.model-option__id{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.model-option__state{color:var(--muted);font-size:11px}
.model-control__footer{justify-content:space-between;margin-top:14px;font-size:11px}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:760px){.model-control__header,.model-control__footer{align-items:stretch;flex-direction:column}.model-control__actions{justify-content:flex-start}.model-list{grid-template-columns:1fr}}
</style>
