# 领域模型与数据库设计

## 1. 设计原则

1. 事件、通知、投递三层分离；
2. 插件采集状态与通知投递状态分离；
3. 所有可恢复任务都有数据库事实来源；
4. 接收人使用平台内部 ID，渠道适配层再解析企业微信 UserID；
5. Secret 与普通配置分离；
6. 幂等约束由数据库唯一索引兜底；
7. 时间统一按 UTC 入库，展示和调度时使用配置时区。

## 2. 核心对象关系

```text
ApiClient 1 ─── N Event
Plugin    1 ─── N PluginRun
Plugin    1 ─── N Event
Event     1 ─── N Notification
Notification 1 ─── N Delivery
Delivery 1 ─── N DeliveryAttempt
Reminder 1 ─── N ReminderRecipient
Reminder 1 ─── N Event
WeComIdentity N ─── 1 Person
ConversationSession N ─── 1 WeComIdentity
```

## 3. Event：发生了什么

事件是来源系统或插件提交的、与渠道无关的事实。

建议字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID/ULID | 事件主键 |
| source_type | enum | api_client/plugin/reminder/system |
| source_id | string | Client ID、Plugin ID 等 |
| event_type | string | 例如 `codex.usage_reset` |
| event_key | string | 来源内稳定幂等键 |
| title | string | 事件标题 |
| content | text | 事件正文 |
| level | enum | info/warning/critical |
| url | string nullable | 跳转链接 |
| image_url | string nullable | 图片 |
| payload | JSON | 来源特有结构化数据 |
| occurred_at | datetime | 事件实际发生时间 |
| accepted_at | datetime | 平台接收时间 |
| status | enum | accepted/routed/ignored/failed |
| ignore_reason | string nullable | 被规则忽略原因 |

唯一约束：

```text
UNIQUE(source_type, source_id, event_key)
```

`event_key` 不能使用随机值。Codex X 插件使用 `x-post-<tweet_id>`。

## 4. Notification：准备发送什么

Notification 是事件经过路由、模板和策略处理后形成的渠道无关消息。

建议字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID/ULID | 主键 |
| event_id | FK nullable | 来源事件 |
| reminder_id | FK nullable | 来源提醒 |
| message_type | enum | text/article/image/voice |
| title | string | 标题 |
| content | text | 正文 |
| url | string nullable | 链接 |
| image_url | string nullable | 图片 URL |
| media_asset_id | FK nullable | 本地媒体资源 |
| priority | enum | normal/high/critical |
| require_ack | bool | 是否需要确认 |
| ack_policy | enum | any/all/each |
| created_at | datetime | 创建时间 |
| expires_at | datetime nullable | 过期后不再投递 |

Notification 不保存企业微信 `touser` 字符串。每个目标形成独立 Delivery。

## 5. Delivery：发给谁、当前状态是什么

每个通知和每个接收目标对应一条 Delivery。

建议字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID/ULID | 主键 |
| notification_id | FK | 通知 |
| channel | string | 首期固定 `wecom` |
| recipient_type | enum | person/broadcast |
| recipient_id | FK/string | 平台内部接收人 ID |
| status | enum | pending/processing/retry_wait/succeeded/dead/cancelled |
| attempt_count | int | 已尝试次数 |
| max_attempts | int | 最大次数 |
| next_attempt_at | datetime | 下次尝试 |
| claimed_at | datetime nullable | Worker 领取时间 |
| claimed_by | string nullable | Worker 标识 |
| sent_at | datetime nullable | 成功时间 |
| last_error_code | string nullable | 归一化错误码 |
| last_error_message | string nullable | 脱敏后的错误信息 |
| acknowledged_at | datetime nullable | 确认时间 |
| acknowledged_by | string nullable | 确认人 |

建议唯一约束：

```text
UNIQUE(notification_id, channel, recipient_type, recipient_id)
```

## 6. DeliveryAttempt：每次尝试发生了什么

字段：

- id；
- delivery_id；
- attempt_number；
- started_at；
- finished_at；
- outcome；
- http_status；
- provider_error_code；
- provider_error_message；
- provider_request_id；
- response_excerpt（脱敏、限长）；
- duration_ms。

不得保存 Access Token、Secret 或完整请求头。

## 7. Person 与 WeComIdentity

### Person

平台内部人员：

- id；
- display_name；
- enabled；
- timezone nullable；
- created_at；
- updated_at。

### WeComIdentity

- id；
- person_id；
- wecom_userid；
- alias nullable；
- enabled；
- discovered_from enum（manual/callback/sync）；
- last_seen_at；
- metadata JSON。

唯一约束：

```text
UNIQUE(wecom_userid)
```

第一阶段可以直接用 WeComIdentity 作为接收人，但领域接口仍应接受内部 `recipient_id`，为后续多渠道保留空间。

## 8. ApiClient

字段：

- id；
- name；
- key_prefix；
- key_hash；
- enabled；
- allowed_event_types JSON nullable；
- allowed_recipient_ids JSON nullable；
- allow_broadcast；
- allow_high_priority；
- rate_limit_per_minute；
- last_used_at；
- created_at；
- revoked_at nullable。

API Key 只在创建时展示一次。数据库只保存哈希和便于识别的前缀。

## 9. Plugin 与 PluginConfig

### Plugin

- id：稳定插件 ID，例如 `codex_x_monitor`；
- name；
- version；
- description；
- enabled；
- install_type enum（builtin/private）；
- status enum（healthy/degraded/failed/disabled）；
- consecutive_failures；
- last_run_at；
- next_run_at；
- last_error；
- manifest JSON；
- created_at；
- updated_at。

### PluginConfig

- plugin_id；
- config JSON；
- schema_version；
- updated_at。

普通配置中只能保存非敏感字段。敏感字段通过 SecretRef 引用。

## 10. PluginSecret

字段：

- id；
- plugin_id nullable；
- owner_type enum（system/plugin/api_client）；
- owner_id；
- name；
- encrypted_value；
- key_version；
- created_at；
- rotated_at nullable。

应用主加密密钥来自环境变量或挂载文件，不进入数据库。

## 11. PluginState

插件私有键值状态：

- plugin_id；
- key；
- value JSON；
- version；
- updated_at。

唯一约束：

```text
UNIQUE(plugin_id, key)
```

更新应支持乐观锁，避免两个并发运行覆盖状态。第一阶段默认每个插件并发数为 1。

## 12. PluginRun

字段：

- id；
- plugin_id；
- trigger_type enum（schedule/manual/startup）；
- status enum（queued/running/succeeded/failed/timed_out/skipped）；
- started_at；
- finished_at；
- duration_ms；
- emitted_event_count；
- cursor_before JSON nullable；
- cursor_after JSON nullable；
- error_type；
- error_message；
- trace_id。

完整异常堆栈进入日志，不建议长期存数据库。

## 13. Reminder

字段：

- id；
- creator_person_id；
- title；
- content；
- schedule_type enum（once/recurring）；
- scheduled_at nullable；
- recurrence_rule nullable；
- timezone；
- next_run_at；
- status enum（draft/active/paused/completed/cancelled/expired）；
- require_ack；
- ack_policy enum（any/all/each）；
- repeat_interval_seconds nullable；
- max_reminders nullable；
- reminder_count；
- stop_at nullable；
- created_at；
- updated_at。

## 14. ReminderRecipient

- reminder_id；
- person_id；
- status enum（pending/acknowledged/expired）；
- acknowledged_at；
- last_notified_at；
- notify_count。

唯一约束：

```text
UNIQUE(reminder_id, person_id)
```

## 15. ConversationSession

字段：

- id；
- wecom_identity_id；
- state；
- draft JSON；
- last_message_at；
- expires_at；
- created_at；
- updated_at。

每个企业微信身份最多一个活跃会话。会话过期后回到 `idle`。

## 16. IncomingMessage

用于审计和异步处理企业微信输入：

- id；
- channel；
- sender_external_id；
- provider_message_id nullable；
- message_type；
- text nullable；
- media_refs JSON；
- received_at；
- processed_at；
- processing_status；
- error_message。

企业微信回调重试时使用 provider 消息标识或内容指纹去重。

## 17. MediaAsset

- id；
- owner_type；
- owner_id；
- media_type；
- storage_path；
- mime_type；
- size_bytes；
- checksum；
- provider_media_id nullable；
- provider_expires_at nullable；
- created_at；
- expires_at nullable。

企业微信临时素材过期后应重新上传，不把 provider media ID 当永久资源。

## 18. 状态转换约束

### Delivery

```text
pending -> processing
processing -> succeeded
processing -> retry_wait
processing -> dead
retry_wait -> pending
pending/retry_wait -> cancelled
```

禁止从 `succeeded` 返回 `pending`。需要再次发送时创建新的 Notification/Delivery。

### Reminder

```text
draft -> active
active -> paused/completed/cancelled/expired
paused -> active/cancelled
```

### Plugin

```text
disabled -> healthy
healthy -> degraded -> failed
failed -> healthy（成功手动运行或重新启用后）
任意状态 -> disabled
```

## 19. 索引建议

必须索引：

- Event `(source_type, source_id, event_key)` unique；
- Delivery `(status, next_attempt_at)`；
- Delivery `(claimed_at)`；
- Reminder `(status, next_run_at)`；
- Plugin `(enabled, next_run_at)`；
- PluginRun `(plugin_id, started_at desc)`；
- IncomingMessage `(provider_message_id)`；
- Notification `(created_at desc)`；
- DeliveryAttempt `(delivery_id, attempt_number)`。

## 20. 数据保留

初始建议：

- Event/Notification/Delivery：保留 180 天；
- DeliveryAttempt：保留 90 天；
- PluginRun：保留 90 天或每插件最近 1000 条；
- IncomingMessage：保留 30 天；
- 媒体文件：按用途 1～30 天；
- Reminder 和确认记录：长期保留，允许归档。

清理任务必须分批执行，避免 SQLite 长事务。
