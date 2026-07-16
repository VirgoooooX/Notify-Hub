import type { AIProvider } from '@/types'

export const presetUrls: Record<string, string> = {
  openai: 'https://api.openai.com/v1',
  gemini: 'https://generativelanguage.googleapis.com/v1beta/openai',
  openrouter: 'https://openrouter.ai/api/v1',
  deepseek: 'https://api.deepseek.com',
  kimi: 'https://api.moonshot.cn/v1',
  dashscope: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  zhipu: 'https://open.bigmodel.cn/api/paas/v4',
  siliconflow: 'https://api.siliconflow.cn/v1',
}

export function defaultAIProviderForm() {
  return {
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
  }
}

export function editAIProviderForm(provider: AIProvider) {
  return {
    ...defaultAIProviderForm(),
    name: provider.name,
    preset: provider.preset,
    base_url: provider.base_url,
    allow_private_network: provider.allow_private_network,
    enabled: provider.enabled,
    timeout_seconds: provider.timeout_seconds,
    max_retries: provider.max_retries,
    verify_tls: provider.verify_tls,
    structured_output_mode: provider.structured_output_mode,
  }
}

export function aiProviderPayload(form: ReturnType<typeof defaultAIProviderForm>) {
  return {
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
}
