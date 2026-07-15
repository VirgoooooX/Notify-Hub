export type Status='active'|'disabled'|'pending'|'processing'|'succeeded'|'retry_wait'|'dead'|'cancelled'|'paused'|'completed'|'failed'|'degraded'|string
export interface Page<T>{items:T[];page:number;page_size:number;total:number}
export interface Dashboard{today_events:number;succeeded_deliveries:number;failed_deliveries:number;retry_wait:number;failed_plugins:number;recent_errors:Array<{id:string;message:string;occurred_at:string;type?:string}>}
export interface Notification{id:string;title:string;content:string;message_type:string;priority:string;status?:Status;created_at:string;event?:Record<string,unknown>;deliveries?:Delivery[]}
export interface Delivery{id:string;recipient_name?:string;recipient_id?:string;status:Status;attempts_count?:number;next_attempt_at?:string;last_error_code?:string;last_error_message?:string;attempts?:Attempt[]}
export interface Attempt{id:string;attempt_no:number;status:Status;started_at:string;finished_at?:string;error_code?:string;error_message?:string}
export interface Person{id:string;name:string;is_default?:boolean;enabled?:boolean;wecom_identities?:Array<{id:string;user_id:string;verified?:boolean}>}
export interface ApiClient{id:string;name:string;key_prefix:string;status:Status;allowed_event_types?:string[];allow_broadcast?:boolean;rate_limit_per_minute?:number;last_used_at?:string}
export interface Plugin{id:string;name:string;version?:string;description?:string;status:Status;enabled:boolean;schedule?:string;last_run_at?:string;next_run_at?:string;consecutive_failures?:number;manifest?:{permissions?:{ai_profiles?:string[]}};secrets?:Array<{name:string;configured:boolean;source?:string;updated_at?:string}>}
export interface AIProvider{id:string;name:string;preset:string;protocol:string;base_url:string;enabled:boolean;allow_private_network:boolean;timeout_seconds:number;max_retries:number;verify_tls:boolean;structured_output_mode:string;api_key_configured:boolean;created_at:string;updated_at:string}
export interface AIProviderModel{id:string;provider_id:string;model_id:string;available:boolean;enabled:boolean;created_at:string;updated_at:string}
export type AICapability = 'classify' | 'extract' | 'summarize'
export type AIOutputLanguage = 'auto' | 'zh-CN' | 'en'
export type AIReasoningEffort = 'provider_default' | 'low' | 'medium' | 'high'
export type AIVerbosity = 'concise' | 'standard' | 'detailed'
export interface AIProfile{id:string;name:string;description:string;capability:AICapability;provider_id:string;model:string;temperature:number;max_output_tokens:number;response_format:string;timeout_seconds:number;output_language:AIOutputLanguage;reasoning_effort:AIReasoningEffort;verbosity:AIVerbosity;include_reason:boolean;max_reason_characters:number;system_instructions:string;cache_ttl_seconds:number;daily_request_limit?:number;daily_token_limit?:number;enabled:boolean;revision:number;created_at:string;updated_at:string}
export interface AIInvocation{id:string;profile_id:string;plugin_id?:string;plugin_run_id?:string;use_case:string;input_hash:string;cache_hit:boolean;status:Status;latency_ms?:number;input_tokens?:number;output_tokens?:number;error_code?:string;created_at:string}
export interface Reminder{id:string;title:string;content?:string;status:Status;schedule_type:'once'|'recurring'|string;next_run_at?:string;timezone?:string;require_ack:boolean;ack_policy?:'any'|'all'|'each';repeat_interval_seconds?:number;max_attempts?:number;attempt_count?:number;stop_at?:string;recipients?:Array<{id:string;name?:string;acknowledged_at?:string}>;timeline?:Array<{id:string;type:string;message:string;occurred_at:string}>}

export type JsonPrimitive = string | number | boolean | null
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue }

export interface PluginConfigSchema {
  type?: 'object' | 'array' | 'string' | 'number' | 'integer' | 'boolean'
  title?: string
  description?: string
  default?: JsonValue
  enum?: JsonPrimitive[]
  properties?: Record<string, PluginConfigSchema>
  items?: PluginConfigSchema
  required?: string[]
  minimum?: number
  maximum?: number
}

export interface PluginDetailsResponse {
  timezone?: string
  config?: Record<string, JsonValue>
  config_schema?: PluginConfigSchema
  schedule?: {
    type?: string
    seconds?: number
  }
}

export interface PluginSecret {
  name: string
  configured: boolean
  source?: string
  updated_at?: string
}
