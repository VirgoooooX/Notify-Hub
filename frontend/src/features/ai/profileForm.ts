import type {
  AICapability,
  AIOutputLanguage,
  AIProfile,
  AIReasoningEffort,
  AIVerbosity,
} from '@/types'

export interface AIProfileFormState {
  id: string
  name: string
  description: string
  capability: AICapability
  provider_id: string
  model: string
  temperature: number
  max_output_tokens: number
  response_format: string
  timeout_seconds: number
  output_language: AIOutputLanguage
  reasoning_effort: AIReasoningEffort
  verbosity: AIVerbosity
  include_reason: boolean
  max_reason_characters: number
  system_instructions: string
  cache_ttl_seconds: number
  daily_request_limit: number | ''
  daily_token_limit: number | ''
  enabled: boolean
}

export function defaultAIProfileForm(providerId = ''): AIProfileFormState {
  return {
    id: '',
    name: '',
    description: '',
    capability: 'classify',
    provider_id: providerId,
    model: '',
    temperature: 0,
    max_output_tokens: 160,
    response_format: 'auto',
    timeout_seconds: 20,
    output_language: 'auto',
    reasoning_effort: 'provider_default',
    verbosity: 'standard',
    include_reason: true,
    max_reason_characters: 200,
    system_instructions: '',
    cache_ttl_seconds: 2592000,
    daily_request_limit: 500,
    daily_token_limit: 1000000,
    enabled: true,
  }
}

export function editAIProfileForm(profile: AIProfile): AIProfileFormState {
  return {
    ...defaultAIProfileForm(profile.provider_id),
    ...profile,
    daily_request_limit: profile.daily_request_limit ?? '',
    daily_token_limit: profile.daily_token_limit ?? '',
  }
}

export function aiProfilePolicyPayload(form: AIProfileFormState) {
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
