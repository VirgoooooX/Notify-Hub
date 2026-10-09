<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '@/lib/api'
import type { AIHubConnection } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import AppButton from '@/components/ui/AppButton.vue'
import AppCard from '@/components/ui/AppCard.vue'
import AppInput from '@/components/ui/AppInput.vue'
import AppCheckbox from '@/components/ui/AppCheckbox.vue'
import AppAlert from '@/components/ui/AppAlert.vue'
import { useUiStore } from '@/stores/ui'
import { useAsyncAction } from '@/composables/useAsyncAction'

const ui = useUiStore()
const loaded = ref(false)
const form = reactive<AIHubConnection>({
  base_url: '', enabled: false, allow_private_network: false,
  timeout_seconds: 600, verify_tls: true, application_key_configured: false,
})
const applicationKey = ref('')
const routing = ref('')
const { pending: busy, run } = useAsyncAction()

async function load() {
  try {
    Object.assign(form, await api.get<AIHubConnection>('/admin/ai/hub'))
    loaded.value = true
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : '连接配置加载失败', 'danger')
  }
}

async function save() {
  try {
    await run(async () => {
      const payload = {
        base_url: form.base_url, enabled: form.enabled,
        allow_private_network: form.allow_private_network,
        timeout_seconds: form.timeout_seconds, verify_tls: form.verify_tls,
      }
      Object.assign(form, await api.put<AIHubConnection>('/admin/ai/hub', payload))
    })
    ui.toast('AI Hub 连接已保存', 'success')
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : '保存失败', 'danger')
  }
}

async function saveKey() {
  try {
    await run(() => api.put('/admin/ai/hub/application-key', { value: applicationKey.value }))
    applicationKey.value = ''
    form.application_key_configured = true
    ui.toast('应用令牌已安全保存', 'success')
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : '令牌保存失败', 'danger')
  }
}

async function testConnection() {
  try {
    await run(async () => {
      const result = await api.post<{ model_count: number }>('/admin/ai/hub/test')
      ui.toast(`AI Hub 连接成功，应用可访问 ${result.model_count} 个模型`, 'success')
    })
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : '连接测试失败', 'danger')
  }
}

async function exportRouting() {
  try {
    routing.value = JSON.stringify(await api.get('/admin/ai/hub/routing'), null, 2)
  } catch (error) {
    ui.toast(error instanceof Error ? error.message : '导出失败', 'danger')
  }
}

onMounted(load)
</script>

<template>
  <PageHeader title="AI Hub 连接" description="Notify Hub 保留业务 Profile；协议、模型授权和回退顺序由 Family AI Hub 管理。" />
  <AppCard v-if="loaded" padding="md">
    <form class="hub-form" @submit.prevent="save">
      <div class="field">
        <label for="hub-url">AI Hub 服务地址</label>
        <AppInput id="hub-url" v-model="form.base_url" required placeholder="https://aihub.example.com" />
        <small>填写服务根地址；内网 HTTP 地址需要开启私网访问。</small>
      </div>
      <div class="field">
        <label for="hub-timeout">调用等待上限（秒）</label>
        <AppInput id="hub-timeout" v-model.number="form.timeout_seconds" type="number" min="1" max="1800" required />
        <small>每次调用以此上限和业务 Profile 超时中较短者为准。</small>
      </div>
      <div class="checks">
        <AppCheckbox v-model="form.enabled">
          启用 AI Hub
        </AppCheckbox>
        <AppCheckbox v-model="form.allow_private_network">
          允许私网访问
        </AppCheckbox>
        <AppCheckbox v-model="form.verify_tls">
          校验 TLS 证书
        </AppCheckbox>
      </div>
      <div class="checks">
        <AppButton type="submit" variant="primary" :loading="busy">
          保存连接
        </AppButton>
        <AppButton :disabled="!form.enabled || !form.application_key_configured || busy" @click="testConnection">
          测试已保存的连接
        </AppButton>
      </div>
    </form>
  </AppCard>

  <AppCard v-if="loaded" padding="md" class="section">
    <form class="hub-form" @submit.prevent="saveKey">
      <div class="field">
        <label for="hub-key">Notify Hub 应用令牌（{{ form.application_key_configured ? '已配置' : '未配置' }}）</label>
        <AppInput id="hub-key" v-model="applicationKey" type="password" autocomplete="new-password" required />
        <small>在 AI Hub 创建 Notify Hub 应用后取得令牌；所有业务 Profile 共用，保存后不再回显。</small>
      </div>
      <AppButton type="submit" variant="primary" :loading="busy">
        安全保存令牌
      </AppButton>
    </form>
  </AppCard>

  <AppCard padding="md" class="section">
    <AppAlert variant="info">
      在 AI Hub 的应用管理中，使用 Notify Hub 业务 Profile 的稳定 ID 配置调用 Profile。新 Profile 也需要在中心选择协议和模型。
    </AppAlert>
    <AppButton class="section" @click="exportRouting">
      导出原有模型路由
    </AppButton>
    <div v-if="routing" class="field section">
      <label for="hub-routing">AI Hub 应用配置</label>
      <small>包含现有 Profile ID 和历史模型选择。请确认模型可用，并补齐空模型列表后用于 AI Hub；不包含提示词或凭据。</small>
      <textarea id="hub-routing" :value="routing" class="input mono" rows="14" readonly />
    </div>
  </AppCard>
</template>

<style scoped>
.hub-form, .field { display: flex; flex-direction: column; gap: var(--space-3); }
.field small { color: var(--text-secondary); }
.checks { display: flex; flex-wrap: wrap; gap: var(--space-4); }
.section { margin-top: var(--space-4); }
</style>
