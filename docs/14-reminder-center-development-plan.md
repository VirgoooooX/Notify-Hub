# Notify Hub 自定义通知与提醒中心

## 完整开发指导与实施计划

**文档状态：** 规划稿
**适用项目：** `VirgoooooX/Notify-Hub`
**核心目标：** 在 Notify Hub 内建设系统级提醒中心，支持自定义文字、图片、图文内容、单次时间、周期、Cron、持续催办和企业微信交互完成，同时提供 Web、企业微信对话、企业微信菜单和插件调用入口。

## 当前实施状态（2026-07-16）

Phase 0～7 的仓库内实现已经完成，Phase 8 的可靠性控制和自动化验收已完成；正式发布仍需在目标部署环境执行外部验收：

* Phase 0：领域边界、Occurrence ADR、迁移方案、调度 Schema 和可注入 Clock 已完成；
* Phase 1：核心模型、单次提醒、Planner、Escalation、管理 API、列表与详情已完成；
* Phase 2：Once/Interval/Cron、IANA 时区、DST、MisfirePolicy、开始/结束时间、未来五次预览和前端编辑器已完成；
* Phase 3：图片上传/验证/去重、提醒引用、渠道媒体缓存、图文发送与引用感知清理已完成；
* Phase 4：Occurrence 级催办、菜单交互、回调幂等、精确取消和明确结果回复已完成；
* Phase 5：企业微信菜单 payload、菜单事件、短时移动身份、移动列表/详情/创建/上传已完成；
* Phase 6：ConversationSession、持久化 ReminderDraft、规则/AI 解析、确认/取消和企微转写文本链路已完成；系统不下载语音或调用 ASR；
* Phase 7：受限 `context.reminders`、Client API、权限、白名单、配额、幂等和审计已完成；
* Phase 8：Worker 恢复、Interaction dead/replay、失败消息重试、引用感知媒体 GC、历史清理 dry-run、队列指标、迁移/备份恢复自动化已完成。

尚未在本机伪造完成的发布证据：有效企业微信测试应用上的普通消息/自定义菜单端到端联调，以及目标文件系统上的 24/72 小时耐久与锁竞争运行。二者属于外部环境验收，不是代码缺口。

---

## 1. 背景与目标

Notify Hub 当前定位是一个模块化单体架构的自托管通知中心，核心平台负责事件接收、消息去重、任务调度、可靠投递和失败重试；插件负责监控外部数据源并向核心提交标准化事件。提醒、通知和接收人也已经被划分为核心领域，而不是某个插件的私有能力。

现阶段需要增加一个完整的“提醒中心”，满足以下业务需求：

1. 用户可以创建纯文字提醒。
2. 用户可以上传图片，创建图片或图文提醒。
3. 用户可以指定单次提醒时间。
4. 用户可以设置固定周期提醒。
5. 用户可以设置五字段 Cron 表达式和时区。
6. 用户可以创建持续催办：

   * 到期后发送提醒；
   * 用户未确认时按指定间隔继续发送；
   * 消息末尾提示用户通过底部【快捷操作】菜单处理；
   * 用户从菜单完成本次提醒后立即停止后续催办。
7. 用户可以通过以下入口创建和管理提醒：

   * Notify Hub Web 后台；
   * 企业微信应用对话；
   * 企业微信自定义菜单；
   * 外部 API；
   * 受权限控制的插件接口。
8. 企业微信语音输入不建设独立语音系统：

   * 只处理企业微信已经转写出的文本；
   * 不下载或保存语音；
   * 不接入 ASR；
   * 不接入 TTS；
   * 不发送语音通知。

---

# 2. 产品边界

## 2.1 系统级提醒中心

自定义提醒必须作为 Notify Hub 的核心业务模块实现，不能作为普通插件。

提醒中心负责：

* 提醒定义；
* 调度规则；
* 提醒执行实例；
* 接收人状态；
* 用户上传媒体；
* 持续催办；
* 完成确认；
* 暂停、恢复和取消；
* Web 和企业微信入口；
* 审计和执行历史。

## 2.2 插件平台

插件平台负责：

* 定时采集外部数据；
* 监控 Twitter、RSS、网页或其他数据源；
* 判断是否出现增量；
* 使用规则或 AI 分析内容；
* 向核心提交事件；
* 在获得明确权限时调用提醒中心。

插件不得：

* 直接写 Reminder 数据表；
* 直接操作企业微信凭据；
* 自己实现独立提醒调度器；
* 自己消费企业微信交互回调；
* 绕过核心通知投递队列；
* 自己维护用户完成状态。

## 2.3 通知中心

通知中心负责：

* 把提醒或事件转换成实际消息；
* 创建 Notification；
* 创建 Delivery；
* 执行可靠投递；
* 记录每次发送尝试；
* 处理重试和死信。

## 2.4 企业微信

企业微信在本架构中承担两类职责：

1. **消息渠道**

   * 接收文字、图文和图片消息。

2. **用户交互入口**

   * 发送文本指令；
   * 使用企业微信自带语音转文字输入文本；
   * 点击菜单；
   * 通过底部【快捷操作】菜单完成、推迟、忽略或停止最近收到的交互式提醒；
   * 打开移动端提醒管理页面。

企业微信不是提醒业务的事实来源。Reminder 数据库状态才是事实来源。

---

# 3. 明确排除的功能

本期不开发：

* 自建 ASR；
* 第三方 ASR API；
* 本地语音识别模型；
* 语音文件下载和持久化；
* 音频转码；
* TTS；
* 语音通知；
* 自定义声音；
* 语音消息历史；
* 说话人识别；
* 语音置信度判断。

语音输入统一按以下方式处理：

```text
用户通过企业微信语音输入功能说话
        ↓
企业微信完成语音转文字
        ↓
Notify Hub 收到普通文本或企微提供的转写文本
        ↓
按照普通文本提醒创建流程处理
```

Notify Hub 内部不需要知道文本是键盘输入还是语音转写。

若企业微信实际回调同时包含音频和已转写文字，适配器只提取转写文字，不下载音频；若企业微信客户端直接将语音输入转换为文字后发送，则按普通文本消息处理。

---

# 4. 总体架构

```text
┌──────────────────── 创建入口 ────────────────────┐
│ Web 后台 │ 企微文本对话 │ 企微菜单 │ 外部 API │ 插件 │
└────────────────────────┬─────────────────────────┘
                         │
                         ▼
              Reminder Application Service
                         │
             创建 / 修改 / 暂停 / 完成
                         │
                         ▼
                  Reminder Definition
                         │
                         ▼
                  Reminder Planner
                         │
             生成 ReminderOccurrence
                         │
                         ▼
             Reminder Escalation Worker
                         │
              创建 Event / Notification
                         │
                         ▼
                 Delivery Queue
                         │
                         ▼
                  WeCom Adapter
                         │
                         ▼
                     企业微信
                         │
                    底部自定义菜单
                         │
                         ▼
               WeCom Callback Adapter
                         │
                         ▼
             Reminder Interaction Service
```

当前通知模型已经支持 `text`、`article`、`image`、`voice` 和历史兼容的 `template_card`，并预留了 `reminder_id`、`media_asset_id`、`ack_policy` 和 `require_ack` 等字段。提醒中心应继续复用现有发送链路；新的交互式提醒只使用 `text`、`article` 或 `image`，不再依赖 `template_card` 按钮。

`voice` 枚举可以暂时保留用于兼容，但本阶段不提供任何创建入口或投递实现。

---

# 5. 核心设计原则

## 5.1 提醒定义和提醒执行实例必须分离

一条周期提醒不能只有一条 Reminder 记录。

例如：

```text
每个工作日上午 9:00 提醒我打卡。
```

这里包含：

* 一条长期有效的 Reminder；
* 每个工作日产生一个独立 ReminderOccurrence。

用户今天点击“已完成”，只能完成今天这一轮，不能永久结束整个 Cron 提醒。

正确模型：

```text
Reminder
  └── ReminderOccurrence 2026-07-16 09:00
  └── ReminderOccurrence 2026-07-17 09:00
  └── ReminderOccurrence 2026-07-20 09:00
```

## 5.2 周期触发与持续催办必须分离

下面的需求包含两层时间逻辑：

> 每个工作日上午九点提醒我打卡，如果没有完成，每五分钟提醒一次。

第一层是产生新一轮提醒：

```text
cron: 0 9 * * 1-5
```

第二层是同一轮提醒的持续催办：

```text
repeat_every: 5 minutes
until: acknowledged
```

不能把持续催办直接实现成每五分钟 Cron，否则无法区分：

* 新的一轮提醒；
* 同一轮提醒的重复发送；
* 今天已经完成；
* 下个工作日仍需重新提醒。

## 5.3 所有入口必须调用同一应用服务

Web、企业微信、API 和插件不得分别实现提醒创建逻辑。

统一调用：

```text
CreateReminderCommand
UpdateReminderCommand
PauseReminderCommand
ResumeReminderCommand
CancelReminderCommand
AcknowledgeOccurrenceCommand
SnoozeOccurrenceCommand
```

这样才能统一执行：

* 权限验证；
* Schema 验证；
* 时间验证；
* 频率限制；
* 接收人验证；
* 审计；
* 幂等处理。

## 5.4 渠道无关

Reminder 不保存完整企业微信 JSON。

核心保存统一内容和动作定义，再由 WeCom Adapter 转换为企业微信消息。

---

# 6. 领域模型

## 6.1 Reminder

表示长期提醒定义。

建议字段：

| 字段                  | 类型                | 说明                                              |
| ------------------- | ----------------- | ----------------------------------------------- |
| `id`                | ULID/UUID         | 主键                                              |
| `name`              | string            | 后台显示名称                                          |
| `title`             | string            | 消息标题                                            |
| `content`           | text              | 消息正文                                            |
| `content_type`      | enum              | text/article/image                              |
| `media_asset_id`    | nullable FK       | 用户上传图片                                          |
| `url`               | nullable string   | 图文跳转地址                                          |
| `status`            | enum              | draft/active/paused/completed/cancelled/expired |
| `schedule_type`     | enum              | once/interval/cron                              |
| `schedule_config`   | JSON              | 调度参数                                            |
| `timezone`          | string            | IANA 时区                                         |
| `start_at`          | nullable datetime | 生效时间                                            |
| `end_at`            | nullable datetime | 结束时间                                            |
| `next_trigger_at`   | nullable datetime | 下一次生成实例时间                                       |
| `misfire_policy`    | enum              | fire_once/skip                                  |
| `require_ack`       | boolean           | 是否需要完成确认                                        |
| `ack_policy`        | enum              | any/all/each                                    |
| `escalation_config` | JSON              | 持续催办参数                                          |
| `created_by_type`   | enum              | admin/wecom/api/plugin                          |
| `created_by_id`     | string            | 创建者                                             |
| `version`           | integer           | 乐观锁版本                                           |
| `created_at`        | datetime          | 创建时间                                            |
| `updated_at`        | datetime          | 更新时间                                            |

### ReminderStatus

```text
draft
active
paused
completed
cancelled
expired
```

对于 Cron 和 Interval 提醒，“完成某个实例”不会把 Reminder 设置为 completed。只有一次性提醒完成、用户主动结束或规则自然终止时才结束定义。

---

## 6.2 ReminderTarget

表示提醒定义上的接收对象。

| 字段            | 说明              |
| ------------- | --------------- |
| `id`          | 主键              |
| `reminder_id` | Reminder        |
| `person_id`   | Notify Hub 内部人员 |
| `active`      | 是否启用            |
| `created_at`  | 创建时间            |

第一阶段只支持 `Person` 接收人。

广播能力可以后续增加，不应在 MVP 中同时引入复杂的部门、标签和群组语义。

---

## 6.3 ReminderOccurrence

表示一次实际执行。

| 字段                        | 说明                                                     |
| ------------------------- | ------------------------------------------------------ |
| `id`                      | 主键                                                     |
| `reminder_id`             | 来源定义                                                   |
| `occurrence_key`          | 全局幂等键                                                  |
| `scheduled_for`           | 原计划执行时间                                                |
| `triggered_at`            | 实际生成时间                                                 |
| `status`                  | scheduled/active/acknowledged/expired/cancelled/failed |
| `title_snapshot`          | 标题快照                                                   |
| `content_snapshot`        | 内容快照                                                   |
| `content_type_snapshot`   | 类型快照                                                   |
| `media_asset_id_snapshot` | 媒体快照                                                   |
| `ack_policy_snapshot`     | 确认策略快照                                                 |
| `escalation_snapshot`     | 催办策略快照                                                 |
| `completed_at`            | 完成时间                                                   |
| `completed_by`            | 完成人                                                    |
| `expires_at`              | 实例失效时间                                                 |
| `version`                 | 乐观锁                                                    |
| `created_at`              | 创建时间                                                   |
| `updated_at`              | 更新时间                                                   |

必须建立唯一约束：

```text
UNIQUE(reminder_id, occurrence_key)
```

建议 `occurrence_key` 使用规范化计划时间生成：

```text
reminder:{reminder_id}:{scheduled_for_utc}
```

防止 Scheduler 重启或重复扫描时生成相同实例。

---

## 6.4 ReminderOccurrenceRecipient

表示某一轮提醒中，某个接收人的独立状态。

| 字段                 | 说明                                     |
| ------------------ | -------------------------------------- |
| `id`               | 主键                                     |
| `occurrence_id`    | ReminderOccurrence                     |
| `person_id`        | 接收人                                    |
| `status`           | pending/acknowledged/expired/cancelled |
| `notify_count`     | 已发送次数                                  |
| `next_notify_at`   | 下次催办时间                                 |
| `last_notified_at` | 最近提醒时间                                 |
| `acknowledged_at`  | 确认时间                                   |
| `acknowledged_by`  | 实际点击人                                  |
| `snoozed_until`    | 延后时间                                   |
| `version`          | 乐观锁                                    |
| `created_at`       | 创建时间                                   |
| `updated_at`       | 更新时间                                   |

唯一约束：

```text
UNIQUE(occurrence_id, person_id)
```

现有持续提醒设计已经明确需要对每个接收人保存确认时间、提醒次数和下一次提醒时间，并支持 `any/all/each` 策略。

---

## 6.5 MediaAsset

用于保存用户上传的图片。

| 字段                  | 说明                             |
| ------------------- | ------------------------------ |
| `id`                | 主键                             |
| `original_filename` | 原始文件名                          |
| `storage_key`       | 内部存储路径                         |
| `content_type`      | MIME 类型                        |
| `size_bytes`        | 文件大小                           |
| `sha256`            | 内容哈希                           |
| `width`             | 图片宽度                           |
| `height`            | 图片高度                           |
| `status`            | pending/ready/rejected/deleted |
| `created_by_type`   | admin/wecom/api                |
| `created_by_id`     | 创建者                            |
| `created_at`        | 创建时间                           |
| `deleted_at`        | 软删除时间                          |

存储目录建议：

```text
data/media/{sha256前两位}/{sha256}
```

不要直接使用用户上传文件名作为磁盘路径。

### 上传限制建议

MVP 默认：

* 只允许 JPEG、PNG、WebP；
* 禁止 SVG；
* 文件大小上限可配置；
* 校验真实文件头，不能只信任扩展名；
* 重新解码图片或至少验证图片结构；
* 计算 SHA-256 去重；
* 删除时先检查引用；
* 不允许用户直接填写任意本地文件路径。

---

## 6.6 ChannelMediaCache

企业微信可能需要先上传媒体，再使用渠道侧媒体标识发送消息。

建议新增缓存：

| 字段                  | 说明     |
| ------------------- | ------ |
| `id`                | 主键     |
| `media_asset_id`    | 本地资源   |
| `channel`           | wecom  |
| `provider_media_id` | 渠道媒体标识 |
| `expires_at`        | 失效时间   |
| `created_at`        | 创建时间   |
| `updated_at`        | 更新时间   |

发送时：

```text
MediaAsset
   ↓
查询 ChannelMediaCache
   ↓
有效：直接使用
无效：重新上传企业微信
   ↓
更新缓存
```

本地 MediaAsset 才是源文件，渠道媒体标识只是临时缓存。

---

## 6.7 最近交互提醒指针与历史 NotificationAction

企业微信菜单没有携带某条消息中的 Action Token。第一版按 UserID 维护：

```text
latest_interactive_occurrence_id
```

该指针表示该用户最近一次**成功发送**的交互式 `ReminderOccurrence`，规则固定为：

* 只有交互式提醒的 Delivery 成功后才更新；
* 普通通知、系统消息和操作确认消息不更新；
* 后续成功发送的新交互式提醒覆盖旧指针；
* 指针指向的实例已终止、无权限或不存在时返回明确结果，不自动回退到更早提醒。

`notification_actions` 和 Action Token 仅作为已投递模板卡片及历史数据的兼容能力保留。新发送的交互式提醒不创建 Action Token，也不通过该表定位菜单操作目标。

| 字段                        | 说明                                |
| ------------------------- | --------------------------------- |
| `id`                      | 主键                                |
| `notification_id`         | 关联通知                              |
| `delivery_id`             | 关联单个投递                            |
| `occurrence_id`           | 对应执行实例                            |
| `occurrence_recipient_id` | 对应接收人                             |
| `action_type`             | reminder.complete/reminder.snooze |
| `token_hash`              | Action Token 哈希                   |
| `expected_person_id`      | 允许操作的人                            |
| `status`                  | active/consumed/expired/revoked   |
| `expires_at`              | 失效时间                              |
| `consumed_at`             | 消费时间                              |
| `consumed_by`             | 实际操作人                             |
| `created_at`              | 创建时间                              |

数据库只存 Token 哈希。

历史企业微信卡片中放置不可预测的完整 Token：

```text
nh.complete.<opaque-token>
```

不能放：

* Reminder ID；
* 数据库自增 ID；
* 企业微信 Secret；
* 用户权限信息；
* 可枚举的短序号。

---

## 6.8 InteractionEvent

保存企业微信菜单事件回调，以及历史模板卡片按钮回调。

| 字段                       | 说明                                               |
| ------------------------ | ------------------------------------------------ |
| `id`                     | 主键                                               |
| `channel`                | wecom                                            |
| `provider_event_type`    | 回调类型                                             |
| `provider_event_key`     | EventKey                                         |
| `provider_response_code` | 历史卡片更新凭据（菜单事件可为空）                                 |
| `sender_external_id`     | 企业微信 UserID                                      |
| `action_id`              | 历史 Action；菜单事件可为空                              |
| `dedupe_key`             | 回调幂等键                                            |
| `status`                 | received/processed/duplicate/rejected/failed     |
| `result`                 | completed/snoozed/ignored/stopped/already_done/unauthorized/expired |
| `received_at`            | 接收时间                                             |
| `processed_at`           | 处理时间                                             |
| `raw_metadata`           | 必要的非敏感元数据                                        |

新菜单流程继续沿用发送者校验、回调去重和业务状态原子修改；目标实例由发送者 UserID 的 `latest_interactive_occurrence_id` 解析。Action Token 和卡片更新流程只保留用于兼容历史模板卡片，不再是当前交互方案的依赖。

---

## 6.9 ConversationSession

用于企业微信分步创建提醒。

| 字段                 | 说明                            |
| ------------------ | ----------------------------- |
| `id`               | 主键                            |
| `channel`          | wecom                         |
| `external_user_id` | 企业微信 UserID                   |
| `intent`           | create_reminder/edit_reminder |
| `state`            | 当前会话阶段                        |
| `draft_id`         | ReminderDraft                 |
| `expires_at`       | 会话超时                          |
| `created_at`       | 创建时间                          |
| `updated_at`       | 更新时间                          |

同一用户默认只能有一个活动创建会话。

---

## 6.10 ReminderDraft

自然语言解析结果不能直接写 Reminder。

先创建草稿：

| 字段                  | 说明                                                        |
| ------------------- | --------------------------------------------------------- |
| `id`                | 主键                                                        |
| `source_type`       | wecom_text/web/api                                        |
| `source_text`       | 原始文本                                                      |
| `parsed_data`       | 结构化结果                                                     |
| `parse_method`      | rules/ai/manual                                           |
| `validation_errors` | 验证错误                                                      |
| `status`            | editing/awaiting_confirmation/confirmed/cancelled/expired |
| `created_by`        | 用户                                                        |
| `expires_at`        | 草稿失效时间                                                    |
| `created_at`        | 创建时间                                                      |
| `updated_at`        | 更新时间                                                      |

只有用户明确确认后，才执行 `CreateReminderCommand`。

---

# 7. 调度模型

插件当前的调度模型只包含 Interval 和 Cron，并描述“插件何时运行”。提醒调度需要额外支持一次性时间，因此不能直接复用插件业务模型。

可以复用底层 Cron 解析、时区计算和 `next_run_at` 算法，但提醒必须拥有独立的类型定义。

## 7.1 OnceSchedule

```json
{
  "type": "once",
  "run_at": "2026-07-17T08:00:00+08:00",
  "timezone": "Asia/Shanghai"
}
```

规则：

* `run_at` 必须是未来时间；
* 数据库存 UTC；
* 保留原始时区；
* 触发后不再计算下一次；
* 完成或过期后 Reminder 结束。

## 7.2 IntervalSchedule

```json
{
  "type": "interval",
  "seconds": 86400,
  "anchor_at": "2026-07-17T08:00:00+08:00",
  "timezone": "Asia/Shanghai"
}
```

规则：

* 必须存在锚点；
* 不使用“上一次 Worker 实际运行时间”作为下一次基准；
* 下一次时间应由锚点计算，避免服务重启后逐渐漂移；
* MVP 最小周期建议为 60 秒；
* Web 普通用户默认不提供一分钟以下周期。

## 7.3 CronSchedule

```json
{
  "type": "cron",
  "expression": "0 9 * * 1-5",
  "timezone": "Asia/Shanghai"
}
```

规则：

* 使用五字段 Cron；
* 必须指定时区；
* 创建和修改时展示未来五次执行时间；
* 拒绝无效表达式；
* 限制过高频率；
* 不允许秒级 Cron；
* 处理夏令时重复或缺失时间。

## 7.4 MisfirePolicy

服务关闭后错过计划时间时，必须定义策略。

### `fire_once`

恢复后立即补发一次，随后继续正常计划。

适用于：

* 服药提醒；
* 重要待办；
* 单次提醒。

### `skip`

跳过错过的执行，等待下一次计划。

适用于：

* 每小时状态报告；
* 高频普通通知；
* 没有补发价值的提醒。

MVP 默认：

* 单次提醒：`fire_once`；
* Cron 和 Interval：`fire_once`；
* 后台允许高级用户改为 `skip`。

不允许恢复后把错过的十几次周期全部补发。

---

# 8. 持续催办模型

## 8.1 EscalationConfig

```json
{
  "repeat_every_seconds": 300,
  "max_notifications": 12,
  "stop_after_seconds": 86400,
  "completion_policy": "any"
}
```

含义：

* 第一次到期立即发送；
* 未完成时每五分钟再发送；
* 最多发送十二次；
* 最长持续二十四小时；
* 任意一个合法接收人完成后停止。

## 8.2 强制保护参数

所有持续提醒至少设置一个停止条件：

* `max_notifications`；
* `stop_after_seconds`；
* 明确的 `expires_at`。

不能创建无限期、无限次数、无任何上限的持续催办。

平台还应设置全局硬限制：

```text
最小催办间隔
单个实例最大通知次数
单条提醒最长持续时间
单用户活动持续提醒上限
每日最大通知量
```

## 8.3 AckPolicy

### `any`

任意合法接收人通过菜单发起“完成本次”：

* 整个 Occurrence 完成；
* 所有接收人的后续催办停止；
* 所有待发送 Delivery 取消。

### `all`

* 每个接收人独立确认；
* 已确认成员停止自己的催办；
* 所有人完成后 Occurrence 完成。

### `each`

* 每人拥有独立结果；
* 整体用于汇总；
* 可允许部分完成后到期。

MVP 推荐：

1. 单接收人；
2. 多接收人 `any`；
3. 再实现 `all`；
4. `each` 放到后续版本。

---

# 9. 状态机

## 9.1 Reminder

```text
draft
  └─ activate → active

active
  ├─ pause → paused
  ├─ cancel → cancelled
  ├─ expire → expired
  └─ one-time finished → completed

paused
  ├─ resume → active
  └─ cancel → cancelled
```

## 9.2 ReminderOccurrence

```text
scheduled
  └─ activate → active

active
  ├─ acknowledge → acknowledged
  ├─ expire → expired
  ├─ cancel → cancelled
  └─ unrecoverable error → failed
```

## 9.3 ReminderOccurrenceRecipient

```text
pending
  ├─ acknowledge → acknowledged
  ├─ expire → expired
  └─ cancel → cancelled
```

所有状态迁移必须由领域服务执行，不允许 Controller 直接修改数据库字段。

---

# 10. Worker 设计

## 10.1 Reminder Planner

职责：

* 扫描 `Reminder.status=active`；
* 查找 `next_trigger_at <= now`；
* 创建 ReminderOccurrence；
* 创建 ReminderOccurrenceRecipient；
* 计算 Reminder 下一次触发时间；
* 保证幂等；
* 处理 MisfirePolicy。

伪流程：

```text
claim due reminders
  ↓
for each reminder:
  calculate scheduled occurrence
  ↓
insert occurrence with unique occurrence_key
  ↓
insert occurrence recipients
  ↓
calculate next_trigger_at
  ↓
commit
```

## 10.2 Reminder Escalation Worker

职责：

* 扫描 `ReminderOccurrenceRecipient.status=pending`；
* 查找 `next_notify_at <= now`；
* 判断是否超过最大次数或截止时间；
* 创建 Notification 和 Delivery；
* 增加 `notify_count`；
* 计算下一次 `next_notify_at`；
* 标记该 Notification 为交互式提醒，供成功投递后更新用户的最近交互提醒指针。

## 10.3 Delivery Worker

继续使用现有投递队列：

```text
Notification
  ↓
Delivery
  ↓
DeliveryAttempt
  ↓
WeCom Adapter
```

提醒中心不能绕过现有 Delivery 机制直接发送。

## 10.4 Interaction Worker

企业微信回调流程：

```text
验签和解密
  ↓
生成 dedupe_key
  ↓
InteractionEvent 先落库
  ↓
快速响应企业微信
  ↓
异步解析菜单动作和发送者 UserID
  ↓
读取 latest_interactive_occurrence_id 并验证发送者身份
  ↓
数据库事务中完成 Occurrence
  ↓
取消待发送 Delivery
  ↓
事务外发送带任务名称的操作结果消息
```

当前设计文档确定“先记录回调、快速返回、异步处理、事务完成、再发送明确结果消息”的链路。历史模板卡片更新仅用于兼容旧消息。

---

# 11. 并发、幂等和事务要求

## 11.1 Scheduler 幂等

通过唯一键保证同一个计划时间不会生成两个 Occurrence。

```text
UNIQUE(reminder_id, occurrence_key)
```

重复插入时应视为已经成功处理，而不是系统错误。

## 11.2 交互回调幂等

重复菜单事件、企业微信重试和历史旧卡片点击必须安全。

同一个菜单事件或历史 Action：

* 第一次：完成业务状态；
* 后续：返回 `already_completed`；
* 不能重复触发副作用；
* 不能生成重复审计记录或重复通知。

## 11.3 完成事务

完成动作必须在同一事务中：

1. 锁定并校验 InteractionEvent；
2. 根据发送者 UserID 读取最近交互提醒指针；
3. 校验发送者与目标接收人的映射和权限；
4. 锁定 OccurrenceRecipient；
5. 更新接收人状态；
6. 重新计算 Occurrence 状态；
7. 清空或调整 `next_notify_at`；
8. 取消相关待发送 Delivery；
9. 标记 InteractionEvent 已处理；
10. 写入 AuditLog。

带任务名称的企业微信操作结果消息在事务外发送。结果消息发送失败不能回滚已经完成的业务状态；历史模板卡片 Action 仍按原锁定、消费和卡片更新逻辑兼容处理。

## 11.4 SQLite 约束

当前项目采用单容器和 SQLite，生产环境应保持单实例部署。

Worker 应采用：

* 短事务；
* 小批量扫描；
* 行状态 claim；
* claim 超时恢复；
* 避免长时间持有写锁；
* 所有外部 HTTP 调用放在事务外。

---

# 12. 通知内容设计

## 12.1 统一内容模型

```json
{
  "type": "article",
  "title": "提交周报",
  "content": "请在下班前提交本周工作总结。",
  "media_asset_id": "asset_xxx",
  "url": null
}
```

支持：

### 纯文字

```text
type=text
title 可选
content 必填
```

### 图文

```text
type=article
title 必填
content 必填
media_asset_id 可选
url 可选
```

### 纯图片

```text
type=image
media_asset_id 必填
content 可选
```

### 交互提醒

交互不是独立内容类型，而是通知行为：

```text
content + require_ack + interactive
```

WeCom Renderer 根据能力选择：

* 普通文字消息；
* 普通图文消息；
* 普通图片消息（必要时附加文字说明）；
* 渠道不支持图文或图片时降级为文字消息。

交互式提醒的文字部分末尾统一追加：

```text
🔁【持续提醒｜需要你确认完成】
这不是一次性通知；在你完成前，系统会按设定间隔继续提醒。
完成后请尽快点击底部【快捷操作】→【完成本次】。
菜单默认操作最近收到的一条交互式提醒。
```

Renderer 不再为新提醒生成模板卡片或消息内按钮。

## 12.2 内容快照

Reminder 修改后，不应改变已经生成的历史 Occurrence。

因此 Occurrence 必须保存：

* 标题快照；
* 正文快照；
* 内容类型快照；
* 媒体引用快照；
* 确认策略快照；
* 持续催办策略快照。

---

# 13. 后端目录规划

建议按当前模块化单体结构增加以下内容：

```text
backend/app/
├── api/
│   ├── admin/
│   │   ├── reminders.py
│   │   └── media_assets.py
│   ├── client/
│   │   └── reminders.py
│   └── wecom/
│       ├── callback.py
│       └── menu.py
│
├── domain/
│   ├── reminders/
│   │   ├── enums.py
│   │   ├── schedules.py
│   │   ├── policies.py
│   │   ├── state_machine.py
│   │   └── exceptions.py
│   ├── media/
│   │   ├── policies.py
│   │   └── exceptions.py
│   └── interactions/
│       ├── actions.py
│       └── exceptions.py
│
├── application/
│   ├── reminders/
│   │   ├── commands.py
│   │   ├── queries.py
│   │   ├── service.py
│   │   ├── planner.py
│   │   └── escalation.py
│   ├── conversations/
│   │   ├── service.py
│   │   └── reminder_parser.py
│   └── interactions/
│       └── service.py
│
├── infrastructure/
│   ├── database/
│   │   ├── models/
│   │   │   ├── reminder.py
│   │   │   ├── media.py
│   │   │   └── interaction.py
│   │   └── repositories/
│   │       ├── reminder_repository.py
│   │       └── media_repository.py
│   ├── media/
│   │   └── local_storage.py
│   └── scheduling/
│       └── cron.py
│
├── integrations/
│   └── wecom/
│       ├── inbound.py
│       ├── renderer.py
│       ├── media.py
│       ├── template_card.py
│       └── menu.py
│
└── workers/
    ├── reminder_planner.py
    ├── reminder_escalation.py
    └── interaction_worker.py
```

如果当前项目仍将 SQLAlchemy 模型集中在 `infrastructure/database/models.py`，第一阶段可以继续集中定义，但不建议继续无限扩充单文件。应在本阶段完成模型拆分，并保证 Alembic 能导入全部模型元数据。

---

# 14. 后端 API 规划

## 14.1 管理端 API

### 创建提醒

```http
POST /api/admin/reminders
```

请求示例：

```json
{
  "name": "工作日打卡",
  "content": {
    "type": "text",
    "title": "打卡提醒",
    "content": "请完成今日上班打卡",
    "media_asset_id": null,
    "url": null
  },
  "schedule": {
    "type": "cron",
    "expression": "0 9 * * 1-5",
    "timezone": "Asia/Shanghai"
  },
  "targets": [
    {
      "person_id": "person_xxx"
    }
  ],
  "acknowledgement": {
    "required": true,
    "policy": "any",
    "repeat_every_seconds": 300,
    "max_notifications": 12,
    "stop_after_seconds": 7200
  },
  "misfire_policy": "fire_once"
}
```

### 列表和详情

```http
GET /api/admin/reminders
GET /api/admin/reminders/{reminder_id}
GET /api/admin/reminders/{reminder_id}/occurrences
GET /api/admin/reminder-occurrences/{occurrence_id}
```

### 修改

```http
PUT /api/admin/reminders/{reminder_id}
```

修改只影响尚未生成的未来 Occurrence。

### 状态操作

```http
POST /api/admin/reminders/{id}/activate
POST /api/admin/reminders/{id}/pause
POST /api/admin/reminders/{id}/resume
POST /api/admin/reminders/{id}/cancel
```

### 手动触发

```http
POST /api/admin/reminders/{id}/trigger
```

用于测试和临时发送，不改变原计划。

### 手动完成实例

```http
POST /api/admin/reminder-occurrences/{id}/acknowledge
```

必须记录管理员身份和操作原因。

## 14.2 调度预览

```http
POST /api/admin/reminders/schedule-preview
```

返回：

```json
{
  "next_runs": [
    "2026-07-17T09:00:00+08:00",
    "2026-07-20T09:00:00+08:00",
    "2026-07-21T09:00:00+08:00",
    "2026-07-22T09:00:00+08:00",
    "2026-07-23T09:00:00+08:00"
  ]
}
```

## 14.3 图片上传

```http
POST /api/admin/media-assets
GET /api/admin/media-assets/{id}
DELETE /api/admin/media-assets/{id}
```

删除正在被提醒引用的图片时返回冲突，不能静默删除。

## 14.4 外部 Client API

```http
POST /api/v1/reminders
GET /api/v1/reminders/{id}
POST /api/v1/reminders/{id}/cancel
```

ApiClient 权限增加：

```text
allow_reminders
allowed_recipient_ids
allow_recurring
allow_cron
allow_interactive
allow_media
max_active_reminders
```

---

# 15. Web 前端规划

## 15.1 一级导航

后台增加一级导航：

```text
工作台
通知
提醒
插件
AI
人员与渠道
设置
```

提醒不能放在插件页面内部。

## 15.2 提醒列表

筛选项：

* 全部；
* 即将触发；
* 等待完成；
* 进行中；
* 已暂停；
* 已完成；
* 已取消；
* 已过期。

显示字段：

* 名称；
* 内容摘要；
* 接收人；
* 调度摘要；
* 下一次触发；
* 是否需要确认；
* 当前状态；
* 最近一次结果；
* 快捷启停按钮。

## 15.3 提醒创建器

推荐四步创建。

### 第一步：内容

* 纯文字；
* 图文；
* 图片；
* 标题；
* 正文；
* 上传图片；
* 可选跳转地址；
* 企业微信消息预览。

### 第二步：时间

* 单次；
* 固定周期；
* Cron；
* 时区；
* 开始时间；
* 结束时间；
* 错过执行策略；
* 未来五次执行预览。

普通模式提供快捷配置：

* 指定日期时间；
* 每天；
* 每周；
* 每月；
* 工作日；
* 每隔若干分钟、小时或天。

高级模式允许编辑 Cron。

### 第三步：接收与交互

* 接收人；
* 是否需要确认；
* 完成策略；
* 催办间隔；
* 最大次数；
* 最长持续时间；
* 过期行为。

### 第四步：确认

展示：

* 最终消息预览；
* 接收人；
* 下一次触发时间；
* 未来五次计划；
* 持续催办说明；
* 风险提示。

## 15.4 详情页面

包含：

* 基本配置；
* 未来计划；
* 历史 Occurrence；
* 每个接收人的状态；
* 通知次数；
* 完成时间；
* Delivery 记录；
* 审计记录；
* 暂停、恢复、编辑、复制和取消。

## 15.5 移动端页面

增加适配企业微信内置浏览器的轻量页面：

```text
/m/reminders/new
/m/reminders/active
/m/reminders/{id}
```

复杂图文、图片上传和 Cron 配置应优先引导到移动 Web，而不是全部通过聊天多轮完成。

---

# 16. 企业微信自定义菜单

交互式提醒使用企业微信应用底部自定义菜单，不依赖模板卡片按钮。第一版菜单固定为三个系列：

```text
新建提醒
├── 快速文字提醒
├── 图文提醒
└── 打开完整创建页

我的提醒
├── 等待我完成
├── 今天的提醒
└── 全部提醒

快捷操作
├── 完成本次
├── 推迟10分钟
├── 推迟30分钟
├── 今日忽略
└── 停止本次
```

## 16.1 菜单职责

【新建提醒】中的“快速文字提醒”启动既有文本创建会话；“图文提醒”和“打开完整创建页”生成绑定当前 UserID 的短期签名移动端链接。【我的提醒】同样生成绑定当前 UserID 的短期签名列表链接，分别打开等待完成、今天和全部范围。移动端链接要求配置 `NOTIFY_HUB_PUBLIC_BASE_URL`，且回调 UserID 必须已关联有效身份。

只有【快捷操作】遵循“最近一条交互式提醒”规则。快捷动作不携带任务编号，系统必须从当前发送者 UserID 对应的 `latest_interactive_occurrence_id` 定位目标；新指针覆盖旧指针，目标失效后不自动回退。

菜单不直接创建新的调度规则，也不承担复杂 Cron 配置；创建和查询项只是既有会话及移动 Web 的 UserID 绑定入口。本次改动不改变插件、普通通知或 Reminder 领域模型。

管理员可先通过 `GET /api/v1/admin/wecom/menu/payload` 预览固定菜单，再通过 `POST /api/v1/admin/wecom/menu/publish` 调用企业微信 `menu/create` API 发布。发布请求沿用 Access Token 缓存和“Token 失效仅刷新重试一次”的渠道规则。

## 16.2 菜单事件统一进入命令层

```text
WeCom Menu Event
   ↓
Inbound Adapter
   ↓
按 UserID 解析 latest_interactive_occurrence_id
   ↓
Reminder Interaction Service
```

不能在回调 Controller 内直接修改 Reminder 或 Occurrence。每个动作完成后必须发送包含任务名称的结果，例如：

```text
✅ 已完成：提交月度报表

本次持续提醒已停止。
```

禁止只回复“操作成功”。

---

# 17. 企业微信文本对话

## 17.1 快速自然语言创建

用户发送：

```text
明天早上八点提醒我带药，如果没有完成，每十分钟提醒一次，最多提醒六次。
```

系统解析为：

```json
{
  "title": "带药",
  "schedule": {
    "type": "once",
    "run_at": "..."
  },
  "acknowledgement": {
    "required": true,
    "repeat_every_seconds": 600,
    "max_notifications": 6
  }
}
```

然后发送确认卡片：

```text
准备创建提醒

内容：带药
首次提醒：明天 08:00
持续催办：每 10 分钟
最多提醒：6 次
接收人：我

[确认创建] [修改] [取消]
```

未经确认不能创建。

## 17.2 分步创建

当一句话无法完整解析时，进入状态机：

```text
awaiting_content
awaiting_schedule
awaiting_image
awaiting_ack_policy
awaiting_confirmation
```

支持文本指令：

```text
取消
重新开始
上一步
不需要持续提醒
改成明天九点
```

## 17.3 图片创建

流程：

```text
用户点击“图文提醒”
  ↓
系统要求发送图片或打开移动页面
  ↓
保存 MediaAsset
  ↓
收集标题和正文
  ↓
收集时间
  ↓
发送确认卡片
  ↓
确认创建
```

如果企业微信图片回调处理复杂或渠道限制较多，MVP 可以只允许通过移动 Web 上传图片，对话中发送图片作为后续增强。

## 17.4 语音输入处理

语音不建立独立会话状态。

用户通过企业微信自带功能得到的文字，统一进入：

```text
handle_inbound_text(user_id, text)
```

不得引入：

```text
handle_audio
download_voice
transcribe_voice
tts_service
voice_asset
```

---

# 18. 自然语言解析与 AI

## 18.1 解析策略

采用两级解析：

```text
确定性规则解析
      ↓ 无法可靠解析
AI 结构化提取
```

规则优先处理：

* 明天上午八点；
* 半小时后；
* 每天九点；
* 每周五；
* 每五分钟；
* 直到完成；
* 最多提醒六次。

AI 只处理复杂或模糊表达。

## 18.2 AI 的职责

AI 只输出结构化草稿：

```json
{
  "intent": "create_reminder",
  "title": "...",
  "content": "...",
  "schedule": {},
  "acknowledgement": {},
  "missing_fields": [],
  "ambiguities": []
}
```

AI 不得：

* 直接写数据库；
* 自动确认创建；
* 绕过时区验证；
* 自行指定未授权接收人；
* 生成无限催办；
* 修改现有 Reminder；
* 直接发送企业微信消息。

## 18.3 AI Profile

提醒解析是平台能力，不属于某个插件。

建议增加系统用途：

```text
capability: reminder_parse
```

或者使用现有结构化提取能力并增加用途标识：

```text
capability: extract
purpose: reminder_parse
```

平台设置指定默认 Profile：

```text
default_reminder_parser_profile_id
```

调用链：

```text
ConversationService
  ↓
ReminderParser
  ↓
AI Gateway
  ↓
JSON Schema 验证
  ↓
ReminderDraft
```

---

# 19. 企业微信普通消息与菜单交互

## 19.1 发送方式

交互式提醒使用普通 `text`、`article` 或 `image` 消息发送，以兼容企业微信客户端和个人微信中的企业微信插件。消息内不放“完成”“推迟”等按钮，统一通过应用底部【快捷操作】菜单发起操作。

消息末尾必须包含菜单提示：

```text
🔁【持续提醒｜需要你确认完成】
这不是一次性通知；在你完成前，系统会按设定间隔继续提醒。
完成后请尽快点击底部【快捷操作】→【完成本次】。
菜单默认操作最近收到的一条交互式提醒。
```

只有交互式提醒成功发送后，才把该 UserID 的 `latest_interactive_occurrence_id` 更新为本次 Occurrence。普通通知、系统消息和操作确认消息不得影响指针。

## 19.2 菜单操作

收到菜单事件后：

1. 验签、解密并持久化回调；
2. 根据发送者 UserID 读取 `latest_interactive_occurrence_id`；
3. 校验该用户仍有权操作指向的 Occurrence；
4. 幂等执行“完成本次”“推迟10分钟”“推迟30分钟”“今日忽略”或“停止本次”；
5. 按业务动作更新接收人/Occurrence 状态并取消不再需要的待发送 Delivery；
6. 写审计日志；
7. 事务外发送带任务名称的明确结果消息。

新的交互式提醒成功发送会覆盖旧指针；指针为空、目标已终止、目标不存在或无权操作时，应明确告知用户，不得回退操作更早的提醒。

结果消息必须包含目标任务名称，例如：

```text
✅ 已完成：提交月度报表

本次持续提醒已停止。
```

模板卡片、消息内按钮、Action Token 和卡片状态更新仅作为历史兼容路径保留，不再用于新交互式提醒。普通消息、自定义菜单及菜单事件字段仍必须通过企业微信官方后台和测试应用完成最小联调，不能只依赖示例 JSON。

## 19.3 Web 管理员全员广播

只有后台 Web 管理员可以创建企业微信 `@all` 广播提醒；企业微信菜单和移动端创建入口始终只允许当前个人。`@all` 与指定接收人 ID 一样，只决定消息发给谁，不决定调度类型、内容类型或是否交互。Once、Interval、Cron 均可选择指定成员或 `@all`，也均可独立选择普通通知或交互式持续提醒。

创建广播提醒时，系统冻结所有启用且已绑定有效企业微信 UserID 的 Person 作为本次接收快照，并在审计日志记录广播标记和人数。企业微信 `@all` 不返回实际收件成员，因此管理员必须确保 Notify Hub 成员清单与企业微信应用可见范围一致。普通广播使用原生 `@all` 发送一次，不更新最近交互提醒指针，也不建立完成反馈。

交互式广播的第一条消息使用原生 `@all`；成功发送后为快照内每个 UserID 更新最近交互提醒指针，后续持续催办只逐人发送给仍未完成的成员，避免已完成人继续收到广播。管理员可按任务选择 `any`、`all` 或 `each` 确认策略。

“全员完成后通知”是单独的可选开关，只在 `@all + 交互式 + all` 时可用。开启后，最后一名快照成员真实完成时，Reminder Worker 通过稳定 Event Key 可靠排队一条新的 `@all` 消息；停止、取消或过期不会触发该消息：

```text
✅ 所有人都已完成：全员提交安全确认

本次广播持续提醒已结束，感谢大家及时确认。
```

广播持续提醒的每一条初始消息和逐人催办都必须带显著标题及说明：

```text
📣【全员持续提醒｜需要每个人确认】
这不是一次性通知；未完成的成员会继续收到催办。
完成后请尽快点击底部【快捷操作】→【完成本次】。
```

仅当管理员开启“全员完成后通知”时，再追加：

```text
所有登记接收人完成后，系统会广播“所有人都完成”。
```

---

# 20. 插件接口

## 20.1 默认插件行为

普通监控插件继续提交 Event：

```python
await context.events.emit(...)
```

核心决定如何路由和通知。

## 20.2 提醒能力

可信插件可在获得权限后调用：

```python
await context.reminders.create(...)
```

Manifest 权限建议：

```yaml
permissions:
  reminders:
    create: true
    allow_recurring: false
    allow_cron: false
    allow_interactive: true
    allow_media: false
    allowed_recipients:
      - self
    max_active: 10
```

## 20.3 插件限制

插件创建提醒时必须限制：

* 接收人；
* 调度频率；
* 活动数量；
* 最大持续时间；
* 最大催办次数；
* 是否可使用 Cron；
* 是否可使用图片；
* 是否可广播。

插件调用最终仍转换为 `CreateReminderCommand`。

---

# 21. 安全设计

## 21.1 企业微信回调

必须：

* 验签；
* 解密；
* 校验 Agent；
* 限制请求体大小；
* 记录回调去重指纹；
* 快速响应；
* 不在请求线程执行耗时外部调用。

## 21.2 菜单交互安全与历史 Action Token

当前菜单流程必须：

* 使用企业微信验签解密后的 UserID 查找最近交互提醒指针；
* 再次校验该 UserID 与目标接收人的映射和权限；
* 对菜单事件建立稳定去重键；
* 不允许客户端提交任意 Occurrence ID；
* 指针失效时不回退到其他任务。

对于仍需处理的历史模板卡片 Action Token：

必须：

* 使用安全随机数；
* 数据库只存哈希；
* 设置有效期；
* 限制预期用户；
* 消费后不可再次使用；
* 旧卡片重复点击幂等返回。

## 21.3 图片安全

必须：

* MIME 与文件头双重校验；
* 文件大小限制；
* 图片解析验证；
* 防止路径穿越；
* 不信任原文件名；
* 不允许 SVG；
* 下载响应设置安全 Content-Type；
* 管理端预览避免执行内嵌内容。

## 21.4 Cron 和频率滥用

必须限制：

* 最小提醒间隔；
* 单用户活动提醒数量；
* 单日通知次数；
* 单条提醒最大接收人数；
* 最大催办次数；
* 最长催办时间；
* 插件创建配额。

## 21.5 权限

角色至少区分：

* 管理员；
* 普通企业微信用户；
* API Client；
* Plugin Runtime。

普通用户只能管理自己创建或发给自己的提醒，除非被授予更高权限。

---

# 22. 审计和可观测性

AuditLog 建议增加以下动作：

```text
reminder.create
reminder.update
reminder.activate
reminder.pause
reminder.resume
reminder.cancel
reminder.trigger
reminder.occurrence.create
reminder.occurrence.acknowledge
reminder.occurrence.expire
reminder.action.consume
reminder.action.reject
media.upload
media.delete
conversation.start
conversation.confirm
conversation.cancel
```

指标建议：

* 活动 Reminder 数；
* 等待完成 Occurrence 数；
* Planner 扫描延迟；
* Escalation 扫描延迟；
* 每小时生成 Occurrence 数；
* 每小时发送提醒数；
* 持续催办平均发送次数；
* 用户完成率；
* 平均确认耗时；
* 回调重复数；
* 菜单操作无权限次数（以及历史 Action 无权限次数）；
* 图片上传失败数；
* 过期提醒数。

日志必须带：

* `request_id`；
* `reminder_id`；
* `occurrence_id`；
* `delivery_id`；
* `interaction_event_id`；
* `actor_id`。

禁止把历史 Action Token、企业微信 Secret 或敏感回调明文写入日志。

---

# 23. 数据迁移

## 23.1 新增表

建议新增：

```text
reminders
reminder_targets
reminder_occurrences
reminder_occurrence_recipients
media_assets
channel_media_cache
notification_actions
interaction_events
conversation_sessions
reminder_drafts
```

其中 `notification_actions` 仅为历史模板卡片兼容表；新菜单交互不再依赖它。UserID 对应的交互状态增加 `latest_interactive_occurrence_id`，但不改变 Reminder、调度、插件和普通 Notification 的领域模型。

## 23.2 修改 Notification

当前 `Notification.reminder_id` 只是字符串字段。建议调整为：

```text
reminder_id nullable FK
reminder_occurrence_id nullable FK
```

Notification 最直接关联 Occurrence，Reminder 关联字段可用于快速查询。

迁移期间：

1. 新增 nullable 外键；
2. 不立即删除旧字段；
3. 新代码双读或只写新字段；
4. 检查历史数据；
5. 后续迁移再删除废弃字段。

## 23.3 索引

至少建立：

```text
reminders(status, next_trigger_at)
reminder_occurrences(reminder_id, scheduled_for)
reminder_occurrences(status, expires_at)
reminder_occurrence_recipients(status, next_notify_at)
notification_actions(token_hash)
notification_actions(status, expires_at)
interaction_events(dedupe_key)
conversation_sessions(channel, external_user_id, expires_at)
media_assets(sha256)
```

---

# 24. 测试计划

## 24.1 单元测试

### 调度

* 单次时间；
* Interval 锚点；
* Cron 下一次时间；
* 时区；
* 夏令时切换；
* MisfirePolicy；
* 开始和结束时间；
* 未来五次预览。

### 状态机

* active → paused；
* paused → active；
* active → cancelled；
* pending → acknowledged；
* 重复完成；
* 已过期实例完成；
* 已取消实例完成。

### 持续催办

* 第一次发送；
* 计算下一次催办；
* 达到最大次数；
* 达到最长时间；
* 完成后停止；
* `any` 策略；
* `all` 策略。

### 内容

* 文字验证；
* 图文验证；
* 图片引用；
* 内容快照。

## 24.2 数据库集成测试

* Planner 重复运行不生成重复 Occurrence；
* 并发回调只完成一次；
* 完成时取消待投递 Delivery；
* Worker 重启后恢复；
* 最近交互提醒指针只在交互式 Delivery 成功后更新；
* 新交互提醒覆盖旧指针且失效后不回退；
* 历史 Action Token 哈希查询和过期 Action 拒绝；
* ConversationSession 超时。

## 24.3 企业微信适配器测试

* 文本消息渲染；
* 图片消息渲染；
* 交互式文字、图文和图片消息渲染及统一菜单提示；
* 回调解析；
* 五种菜单事件及 UserID 身份映射；
* 重复回调；
* 操作结果包含任务名称；
* 普通通知、系统消息、操作确认消息不覆盖最近交互提醒指针；
* 企业微信已转写文本进入普通文本处理链。

不测试和不实现：

* 音频下载；
* ASR；
* TTS；
* 语音投递。

## 24.4 API 测试

* 创建、修改、暂停、恢复、取消；
* 非法 Cron；
* 过去的单次时间；
* 过高频率；
* 无权限接收人；
* 缺少停止条件的持续提醒；
* 图片类型非法；
* 图片过大；
* 乐观锁冲突。

## 24.5 前端测试

* 创建器四步流程；
* 表单恢复；
* Cron 预览；
* 图片上传；
* 持续催办参数联动；
* 提交失败展示；
* 移动端布局；
* 暂停和恢复；
* 历史实例查看。

## 24.6 端到端验收

核心 E2E：

```text
Web 创建持续提醒
  ↓
到期生成 Occurrence
  ↓
企业微信收到带菜单提示的普通文字/图文/图片消息
  ↓
五分钟后再次收到
  ↓
用户点击底部【快捷操作】→【完成本次】
  ↓
后续不再发送
  ↓
用户收到包含任务名称的完成结果，后台显示完成时间和操作人
```

企业微信文本创建 E2E：

```text
用户发送：
“明天八点提醒我带药，没完成就每十分钟提醒一次”

系统返回确认卡片
  ↓
用户确认
  ↓
后台出现 Reminder
  ↓
到期正常发送
```

---

# 25. 分阶段开发计划

## Phase 0：架构决策与基础整理

### 任务

* 新增 Reminder Center ADR；
* 明确 Reminder 和 Plugin 的边界；
* 确定调度 JSON Schema；
* 确定时区和 MisfirePolicy；
* 确定 Notification 外键迁移方案；
* 确定图片大小和格式限制；
* 明确本期无 ASR/TTS；
* 建立可注入 Clock，禁止业务代码直接散落调用当前时间。

### 验收

* 架构文档合并；
* 所有领域枚举确定；
* Alembic 迁移方案通过评审；
* 无未决的核心表结构问题。

---

## Phase 1：提醒核心模型与单次文字提醒

### 任务

* Reminder；
* ReminderTarget；
* ReminderOccurrence；
* ReminderOccurrenceRecipient；
* OnceSchedule；
* Reminder Planner；
* Reminder Escalation Worker；
* 管理端 CRUD；
* 纯文字 Notification；
* 列表和详情基础页面。

### 验收

* Web 可创建未来单次文字提醒；
* 到期只生成一个 Occurrence；
* 重启后仍会触发；
* 重复扫描不重复发送；
* 可暂停、恢复和取消；
* 历史实例可查询。

---

## Phase 2：Interval、Cron 与调度预览

### 任务

* IntervalSchedule；
* CronSchedule；
* 时区；
* 下一次执行计算；
* 未来五次预览；
* MisfirePolicy；
* 开始和结束时间；
* 前端时间创建器；
* Cron 高级编辑器。

### 验收

* 每日、每周、工作日和自定义 Cron 可用；
* 服务重启不产生计划漂移；
* 错过执行按策略处理；
* 不会批量补发大量历史实例；
* 前端正确展示未来五次执行时间。

---

## Phase 3：图片和图文提醒

### 任务

* MediaAsset；
* 本地文件存储；
* 图片验证；
* SHA-256 去重；
* ChannelMediaCache；
* 图片上传 API；
* 前端上传和预览；
* 图文 Renderer；
* 历史内容快照。

### 验收

* 用户可上传图片；
* 可创建图文提醒；
* 同一图片可复用；
* 渠道媒体过期后可重新上传；
* 删除被引用图片时正确阻止；
* 非法图片被拒绝。

---

## Phase 4：持续催办和菜单交互

### 任务

* EscalationConfig；
* InteractionEvent；
* 普通文字/图文/图片交互提醒及菜单提示；
* 按 UserID 维护 `latest_interactive_occurrence_id`；
* 五种自定义菜单动作；
* 回调幂等；
* 用户身份校验；
* 待发送 Delivery 取消；
* 带任务名称的操作结果回复；
* 历史 NotificationAction/模板卡片兼容；
* 单接收人和 `any` 策略。

### 验收

* 未完成时按间隔继续提醒；
* 达到上限后自动停止；
* 菜单完成本次后不再发送；
* 重复菜单事件不产生副作用；
* 非接收人发起菜单动作被拒绝；
* 只有成功发送的交互式提醒更新指针，新覆盖旧且不回退；
* 普通通知、系统消息和确认消息不更新指针；
* 操作结果明确包含任务名称；
* 后台显示完成时间和完成人。

---

## Phase 5：企业微信菜单和移动 Web

### 任务

* 自定义菜单事件；
* 快速文字提醒；
* 我的提醒；
* 等待完成列表；
* 移动创建页；
* 移动详情页；
* 菜单跳转和身份映射。

### 验收

* 用户可以从企微菜单进入创建页面；
* 可查看等待自己完成的提醒；
* 移动端可上传图片并创建提醒；
* 用户只能访问授权提醒。

---

## Phase 6：企业微信文本对话创建

### 任务

* ConversationSession；
* ReminderDraft；
* 规则解析；
* AI 结构化提取；
* 确认卡片；
* 修改、取消和重新开始；
* 企微已转写文字统一处理。

### 验收

* 键盘文字和企微语音转写文字行为一致；
* 未确认的草稿不创建 Reminder；
* 模糊时间会向用户确认；
* 会话超时自动清理；
* AI 不可直接写数据库；
* 系统不下载和保存语音。

---

## Phase 7：插件和外部 API

### 任务

* `context.reminders`；
* 插件 Manifest 权限；
* Client API 权限；
* 配额；
* 审计；
* 接收人白名单；
* Cron 和持续提醒权限。

### 验收

* 无权限插件不能创建提醒；
* 插件不能直接访问数据库；
* 插件创建的提醒进入相同调度和投递链路；
* 达到配额时明确拒绝；
* 所有插件创建动作有审计记录。

---

## Phase 8：可靠性与正式发布

### 任务

* 压力和长时间运行测试；
* SQLite 锁竞争检查；
* Worker 崩溃恢复；
* Delivery 和 Interaction 死信处理；
* 数据清理任务；
* 媒体垃圾回收；
* 指标和监控；
* 完整运维文档；
* 备份恢复验证。

### 验收

* 重启不丢提醒；
* Worker 异常不重复执行；
* 回调重试安全；
* 数据库备份恢复后调度正常；
* 孤立媒体可安全清理；
* 全部质量门禁通过。

---

# 26. MVP 范围

第一版可正式使用的 MVP 应包含：

* Web 创建文字提醒；
* Web 上传单张图片；
* 单次、Interval 和 Cron；
* 单接收人；
* 时区；
* 暂停、恢复和取消；
* 持续催办；
* 最大次数和最长时间；
* 企业微信普通文字/图文/图片交互提醒；
* 企业微信底部三系列菜单及【快捷操作】；
* 菜单完成本次后停止催办；
* 企业微信文本创建；
* 企业微信自带语音转文字后的文本创建；
* 执行历史和审计。

MVP 不包含：

* ASR；
* TTS；
* 语音消息；
* 多图片；
* 富文本编辑器；
* 部门和标签广播；
* `all/each` 高级确认策略；
* 复杂工作流；
* 小程序；
* 日历同步；
* 地理位置触发；
* 第三方插件市场。

---

# 27. Definition of Done

每个阶段完成必须同时满足：

1. 数据库迁移可从空库执行；
2. 数据库迁移可从当前正式版本升级；
3. 后端 Ruff、Mypy 和 Pytest 通过；
4. 前端 lint、typecheck、test 和 build 通过；
5. API 有请求和响应 Schema；
6. 状态迁移有单元测试；
7. 时间逻辑使用可注入 Clock；
8. 外部请求不在数据库事务中执行；
9. 所有写操作产生审计日志；
10. 重复请求或回调具备幂等性；
11. 权限失败返回明确错误；
12. 页面适配桌面和企业微信移动端；
13. 文档和示例同步更新；
14. 不引入 ASR、TTS 或语音文件链路；
15. 最小 E2E 场景通过。

---

# 28. 最终模块关系

```text
Notify Hub
├── 通知中心
│   ├── Notification
│   ├── Delivery
│   └── DeliveryAttempt
│
├── 提醒中心
│   ├── Reminder
│   ├── ReminderOccurrence
│   ├── ReminderOccurrenceRecipient
│   ├── Scheduler
│   ├── Escalation
│   └── Interaction
│
├── 媒体中心
│   ├── MediaAsset
│   └── ChannelMediaCache
│
├── 企业微信适配
│   ├── 文字和图片发送
│   ├── 普通文字、图文和图片消息
│   ├── 回调
│   ├── 自定义菜单
│   └── 已转写文本输入
│
├── AI 网关
│   └── 自然语言提醒结构化提取
│
└── 插件中心
    ├── 外部数据监控
    ├── AI 内容判定
    ├── Event 提交
    └── 受限 Reminder 创建
```

最终职责原则：

> 插件负责发现事情，提醒中心负责安排事情，通知中心负责可靠地告诉用户，企业微信负责承载消息和用户交互，AI 只负责把自然语言转换成可确认的结构化草稿。

语音输入只使用企业微信提供的转文字结果，Notify Hub 不建设任何独立语音处理能力。
