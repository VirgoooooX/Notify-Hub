export type Status='active'|'disabled'|'pending'|'processing'|'succeeded'|'retry_wait'|'dead'|'cancelled'|'paused'|'completed'|'failed'|'degraded'|string
export interface Page<T>{items:T[];page:number;page_size:number;total:number}
export interface Dashboard{today_events:number;succeeded_deliveries:number;failed_deliveries:number;retry_wait:number;failed_plugins:number;recent_errors:Array<{id:string;message:string;occurred_at:string;type?:string}>}
export interface Notification{id:string;title:string;content:string;message_type:string;priority:string;status?:Status;created_at:string;event?:Record<string,unknown>;deliveries?:Delivery[]}
export interface Delivery{id:string;recipient_name?:string;recipient_id?:string;status:Status;attempts_count?:number;next_attempt_at?:string;last_error_code?:string;last_error_message?:string;attempts?:Attempt[]}
export interface Attempt{id:string;attempt_no:number;status:Status;started_at:string;finished_at?:string;error_code?:string;error_message?:string}
export interface Person{id:string;name:string;is_default?:boolean;enabled?:boolean;wecom_identities?:Array<{id:string;user_id:string;active?:boolean;verified?:boolean}>}
export interface ApiClient{id:string;name:string;key_prefix:string;status:Status;allowed_event_types?:string[];allowed_recipient_ids?:string[];allow_broadcast?:boolean;allow_media?:boolean;allow_reminders?:boolean;allow_recurring?:boolean;allow_cron?:boolean;allow_interactive?:boolean;max_active_reminders?:number;rate_limit_per_minute?:number;last_used_at?:string}
export type PluginSchedule =
  | { type: 'interval'; seconds: number }
  | { type: 'cron'; expression: string; timezone: string }
export type PluginScheduleMode = 'default' | 'interval' | 'cron'
export interface PluginScheduleFormState {
  schedule_mode: PluginScheduleMode
  schedule_interval_minutes: number
  schedule_cron_expression: string
  schedule_timezone: string
}
export interface Plugin{id:string;name:string;version?:string;description?:string;status:Status;enabled:boolean;schedule?:PluginSchedule;schedule_inherits_default?:boolean;last_run_at?:string;next_run_at?:string;consecutive_failures?:number;manifest?:{default_schedule?:PluginSchedule;permissions?:{ai_profiles?:string[];ai_capabilities?:AICapability[]}};secrets?:Array<{name:string;configured:boolean;source?:string;updated_at?:string}>}
export interface AIProvider{id:string;name:string;preset:string;protocol:string;base_url:string;enabled:boolean;allow_private_network:boolean;timeout_seconds:number;max_retries:number;verify_tls:boolean;structured_output_mode:string;api_key_configured:boolean;created_at:string;updated_at:string}
export interface AIProviderModel{id:string;provider_id:string;model_id:string;available:boolean;enabled:boolean;created_at:string;updated_at:string}
export type AICapability = 'classify' | 'extract' | 'summarize'
export type AIOutputLanguage = 'auto' | 'zh-CN' | 'en'
export type AIReasoningEffort = 'provider_default' | 'low' | 'medium' | 'high'
export type AIVerbosity = 'concise' | 'standard' | 'detailed'
export interface AIProfile{id:string;name:string;description:string;capability:AICapability;provider_id:string;model:string;temperature:number;max_output_tokens:number;response_format:string;timeout_seconds:number;output_language:AIOutputLanguage;reasoning_effort:AIReasoningEffort;verbosity:AIVerbosity;include_reason:boolean;max_reason_characters:number;system_instructions:string;cache_ttl_seconds:number;daily_request_limit?:number;daily_token_limit?:number;enabled:boolean;revision:number;created_at:string;updated_at:string}
export interface AIInvocation{id:string;profile_id:string;plugin_id?:string;plugin_run_id?:string;use_case:string;input_hash:string;cache_hit:boolean;status:Status;latency_ms?:number;input_tokens?:number;output_tokens?:number;error_code?:string;created_at:string}
export interface ReminderOccurrenceRecipient{id:string;person_id:string;name?:string;status:Status;notify_count:number;next_notify_at?:string;last_notified_at?:string;acknowledged_at?:string;acknowledged_by?:string;latest_interactive_user_ids?:string[]}
export interface ReminderOccurrence{id:string;occurrence_key:string;scheduled_for:string;triggered_at:string;status:Status;title:string;content:string;content_type:string;media_asset_id?:string;completed_at?:string;completed_by?:string;expires_at?:string;recipients:ReminderOccurrenceRecipient[]}
export interface Reminder{id:string;title:string;content?:string;content_type?:'text'|'image'|'article'|string;media_asset_id?:string;url?:string;status:Status;schedule_type:'once'|'interval'|'cron'|'recurring'|string;schedule_config?:Record<string,unknown>;next_run_at?:string;timezone?:string;start_at?:string;end_at?:string;misfire_policy?:'fire_once'|'skip';broadcast?:boolean;notify_on_all_completed?:boolean;require_ack:boolean;interaction_mode?:'latest_menu'|'none';ack_policy?:'any'|'all'|'each';repeat_interval_seconds?:number;max_attempts?:number;attempt_count?:number;stop_at?:string;escalation_stop_after_seconds?:number;recipients?:Array<{id:string;name?:string;status?:Status;acknowledged_at?:string;attempt_count?:number}>;timeline?:Array<{id:string;type:string;message:string;occurred_at:string}>;occurrences?:ReminderOccurrence[]}

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
  manifest?: { default_schedule?: PluginSchedule }
  schedule?: PluginSchedule
  schedule_inherits_default?: boolean
}

export interface PluginSecret {
  name: string
  configured: boolean
  source?: string
  updated_at?: string
}
