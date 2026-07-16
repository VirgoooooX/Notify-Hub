# 可交互持续提醒设计

## 1. 结论

Notify Hub 可以实现以下完整交互：

```text
创建持续提醒
  -> 每隔 5 分钟发送一次企业微信普通文字、图文或图片消息
  -> 消息提示用户使用底部【快捷操作】菜单
  -> 用户点击“完成本次”等菜单项
  -> 企业微信将菜单 click 事件回调给 Notify Hub
  -> Notify Hub 原子地确认任务完成
  -> 取消尚未发送的后续提醒
  -> 返回包含任务名称的明确操作结果
```

该能力应作为 `v0.2.0 / Phase 8` 的核心验收场景，而不是普通文本提醒的附加选项。

自 ADR-024 起，新交互式提醒必须使用普通 `text`、`news` 或 `image + text` 消息，并通过企业微信应用底部自定义菜单操作。下文的模板卡片、Action Token 与卡片更新内容仅保留为历史兼容说明，不再用于新提醒。

---

## 2. 用户体验

### 2.1 创建提醒

用户可以通过管理后台、外部 API 或后续企业微信对话创建：

```text
每隔 5 分钟提醒我关闭燃气阀，直到我点击完成。
```

平台展示确认信息：

```text
准备创建持续提醒：

事项：关闭燃气阀
首次提醒：立即
提醒间隔：5 分钟
停止条件：点击“已完成”
最长持续：24 小时
最多提醒：12 次
接收人：Vigoss
```

### 2.2 提醒消息

每次催办使用普通消息，并在末尾显示：

```text
关闭燃气阀

🔁【持续提醒｜需要你确认完成】
这不是一次性通知；在你完成前，系统会按设定间隔继续提醒。
完成后请尽快点击底部【快捷操作】→【完成本次】。
菜单默认操作最近收到的一条交互式提醒。
```

### 2.3 点击完成

用户点击后：

1. 后台立即把该用户的提醒状态改为已确认；
2. 阻止生成新的提醒投递；
3. 取消仍处于 `pending` 或 `retry_wait` 的后续投递；
4. 发送包含任务名称的确认消息；
5. 管理后台记录确认人、确认时间和对应 occurrence。

更新后的卡片建议显示：

```text
✅ 已完成
完成时间：2026-07-14 15:32
完成成员：Vigoss
```

---

## 3. 企业微信能力映射

### 3.1 旧模板卡片出站消息（仅历史兼容）

旧版本曾发送：

```json
{
  "touser": "Vigoss",
  "msgtype": "template_card",
  "agentid": 1000002,
  "template_card": {
    "card_type": "button_interaction",
    "source": {
      "desc": "Notify Hub"
    },
    "main_title": {
      "title": "持续提醒",
      "desc": "关闭燃气阀"
    },
    "sub_title_text": "已提醒 3 次，下次提醒将在 5 分钟后发送",
    "button_list": [
      {
        "type": 0,
        "text": "已完成",
        "style": 1,
        "key": "nh.complete.<opaque-action-token>"
      }
    ]
  }
}
```

要求：

- `button_list.type=0` 表示点击后产生回调事件；
- `key` 会作为回调 `EventKey` 返回；
- 每张卡片使用不同的不可预测 action token；
- 不把数据库自增 ID、企业微信 Secret 或权限信息直接放入 `key`；
- 卡片发送失败仍按 Delivery 的正常重试策略处理。

### 3.2 旧模板卡片入站事件（仅历史兼容）

用户点击后，企业微信向已经配置的回调地址推送：

```text
MsgType=event
Event=template_card_event
EventKey=nh.complete.<opaque-action-token>
FromUserName=Vigoss
ResponseCode=<provider-response-code>
CardType=button_interaction
```

Notify Hub 必须解析并保存：

- 企业微信发送者 UserID；
- EventKey；
- ResponseCode；
- CardType；
- AgentID；
- CreateTime；
- 原始回调的去重指纹。

### 3.3 旧模板卡片更新（仅历史兼容）

业务状态确认成功后，调用企业微信：

```text
POST /cgi-bin/message/update_template_card
```

使用回调携带的 `response_code` 更新被点击的卡片，建议：

```json
{
  "userids": ["Vigoss"],
  "agentid": 1000002,
  "response_code": "<response-code>",
  "button": {
    "replace_name": "已完成"
  }
}
```

也可以用完整模板替换，把卡片更新成不可操作的完成状态。

注意：

- `response_code` 有有效期，并且只能使用一次；
- 卡片更新失败不能回滚业务完成状态；
- 更新失败时可以降级发送普通文本：“任务已完成”；
- 业务数据库是事实来源，企业微信卡片外观只是展示状态。

---

## 4. 核心领域模型

### 4.1 Reminder

持续提醒至少包含：

| 字段 | 说明 |
|---|---|
| `id` | 提醒 ID |
| `title` | 标题 |
| `content` | 正文 |
| `status` | active/paused/completed/cancelled/expired |
| `require_ack` | 固定为 true |
| `ack_policy` | any/all/each |
| `repeat_interval_seconds` | 例如 300 |
| `max_reminders` | 最大催办次数 |
| `reminder_count` | 已生成次数 |
| `next_run_at` | 下次触发时间 |
| `stop_at` | 最晚停止时间 |
| `completed_at` | 整体完成时间 |
| `version` | 乐观锁版本 |

### 4.2 ReminderRecipient

每个接收人独立保存：

| 字段 | 说明 |
|---|---|
| `reminder_id` | 提醒 ID |
| `person_id` | 接收人 |
| `status` | pending/acknowledged/expired/cancelled |
| `acknowledged_at` | 确认时间 |
| `acknowledged_by` | 实际点击人 |
| `last_notified_at` | 最近发送时间 |
| `notify_count` | 已收到催办次数 |
| `next_run_at` | 该接收人的下次催办时间 |

建议把 `next_run_at` 下沉到 `ReminderRecipient`，因为多接收人和 `each/all` 策略下，不同成员可能在不同时间停止。

### 4.3 NotificationAction

新增通知动作表，用于映射卡片按钮：

| 字段 | 说明 |
|---|---|
| `id` | ULID/UUID |
| `notification_id` | 对应通知 |
| `delivery_id` | 对应单个接收人的投递 |
| `reminder_id` | 对应提醒 |
| `reminder_recipient_id` | 对应接收人状态 |
| `action_type` | `reminder.complete`，后续可扩展 snooze/cancel |
| `token_hash` | action token 的哈希 |
| `status` | active/consumed/expired/revoked |
| `expected_person_id` | 允许操作的人 |
| `expires_at` | 动作有效期 |
| `consumed_at` | 首次成功执行时间 |
| `consumed_by` | 实际操作人 |
| `created_at` | 创建时间 |

数据库只保存 token 哈希。完整 token 仅在构建卡片时短暂使用。

### 4.4 InteractionEvent

保存回调处理记录：

| 字段 | 说明 |
|---|---|
| `id` | 主键 |
| `channel` | wecom |
| `provider_event_type` | template_card_event |
| `provider_event_key` | EventKey |
| `provider_response_code` | ResponseCode，加密或按短期保留策略处理 |
| `sender_external_id` | 企业微信 UserID |
| `action_id` | 解析得到的 NotificationAction |
| `status` | received/processed/duplicate/rejected/failed |
| `result` | completed/already_completed/unauthorized/expired |
| `received_at` | 收到时间 |
| `processed_at` | 处理时间 |
| `dedupe_key` | 回调幂等键 |

`response_code` 只为更新卡片所需，不应长期保留。成功更新或超过短期保留时间后应清除。

---

## 5. 状态机

### 5.1 单接收人

```text
active / recipient=pending
  -> 到期发送卡片
  -> awaiting_ack
  -> 5 分钟后再次发送
  -> 用户点击“已完成”
  -> recipient=acknowledged
  -> reminder=completed
  -> next_run_at=NULL
```

### 5.2 多接收人策略

#### `any`

任意一个授权成员点击完成：

- 整个 Reminder 立即完成；
- 所有接收人停止后续催办；
- 尚未发送的 Delivery 全部取消。

#### `all`

- 每个成员点击后只停止自己的催办；
- 所有人都确认后 Reminder 才变为 completed；
- 未确认成员继续接收提醒。

#### `each`

- 每个接收人拥有独立完成状态；
- 每人点击后停止自己的催办；
- 整体状态可在所有接收人终止后完成；
- 与 `all` 的差异主要用于后续业务汇总和超时处理策略，代码中必须写清语义。

MVP 推荐先实现：

1. 单接收人；
2. 多接收人的 `any`；
3. 再实现 `all/each`。

---

## 6. 点击处理流程

```text
企业微信回调
  -> 验签与解密
  -> 解析 template_card_event
  -> 生成回调 dedupe_key
  -> InteractionEvent 先落库
  -> 快速返回企业微信
  -> Interaction Worker 异步处理
  -> 解析 action token
  -> 查询 NotificationAction
  -> 校验发送者权限
  -> 数据库事务内完成提醒
  -> 取消待发送 Delivery
  -> 更新企业微信卡片
  -> 记录审计与处理结果
```

### 6.1 权限校验

点击者必须满足至少一项：

- 是该 ReminderRecipient 对应成员；
- 是具备代办完成权限的管理员；
- `ack_policy=any` 且点击者属于该提醒的合法接收人。

不能只凭 EventKey 找到任务后直接完成，必须同时校验 `FromUserName`。

### 6.2 幂等处理

以下情况必须安全返回成功语义：

- 企业微信重复回调同一次点击；
- 用户连续点击；
- 用户点击旧的催办卡片；
- 两个合法成员同时点击 `any` 策略任务；
- 管理后台和企业微信同时完成任务。

推荐结果：

```text
首次有效点击       -> completed
重复点击           -> already_completed
旧卡片点击         -> already_completed，并尝试更新当前旧卡片
无权限用户点击     -> rejected
过期 token         -> expired
无效 token         -> rejected
```

重复操作不得重新生成事件、重复写入完成时间或重新触发业务副作用。

---

## 7. 调度与并发竞态

最关键的竞态是：

```text
Reminder Worker 正准备发送下一次提醒
          与
用户点击“已完成”
```

### 7.1 强制约束

Reminder Worker 在创建下一次 Notification 前，必须在事务中重新检查：

- Reminder 仍为 active；
- ReminderRecipient 仍为 pending；
- `next_run_at <= now`；
- 未超过 max_reminders；
- 未超过 stop_at；
- 当前 version 与 claim 时一致。

### 7.2 Delivery 发送前二次检查

对于 `require_ack=true` 的催办 Delivery，Delivery Worker 在真正调用企业微信前必须再次检查对应 ReminderRecipient 是否仍为 pending。

如果已确认：

```text
Delivery.status = cancelled
cancel_reason = reminder_acknowledged
```

### 7.3 可接受的边界

在极窄竞态窗口中，企业微信请求已经发出而用户同时点击完成，可能仍多到达一条卡片。系统必须保证：

- 点击后不再创建新的后续提醒；
- 已经入网的最后一条卡片即使到达，点击后只返回已完成；
- 不因为追求绝对零竞态而在数据库事务中执行网络请求。

文档和 UI 不应承诺“点击后绝不会再收到任何已经在途的消息”，应承诺“点击后停止生成后续提醒”。

---

## 8. 旧卡片处理

持续提醒每 5 分钟产生一张卡片，因此用户可能点击任意历史卡片。

设计要求：

- 每张卡片的 action token 唯一；
- 所有 token 都关联同一个 ReminderRecipient；
- 任意未过期历史 token 都可以完成任务；
- 首次完成后，其余 token 逻辑上立即失效；
- 后续点击旧卡片返回 `already_completed`；
- 若该次回调携带有效 response_code，可把该张旧卡片也更新为“已完成”。

不需要主动遍历并更新所有历史卡片。这样会增加 API 调用、失败点和状态复杂度。

---

## 9. 安全与保护限制

### 9.1 默认限制

- 最短催办间隔：5 分钟；
- 默认最大催办次数：12；
- 默认最长持续时间：24 小时；
- 默认只允许明确接收人，不允许广播；
- 无限催办必须管理员二次确认；
- 单个提醒的活跃 action token 数量设置上限；
- action token 至少包含 128 bit 随机熵；
- 回调接口必须验签和解密；
- EventKey 不记录到普通 info 日志；
- response_code 不进入长期日志。

### 9.2 防刷屏

同一 ReminderRecipient：

- 不允许同时存在两个未完成的本轮催办生成任务；
- 立即运行和调度触发必须共享同一个 claim 机制；
- 修改间隔后不能补发历史周期；
- 服务恢复时只生成一条到期提醒，不按错过次数批量补发；
- 企业微信限流时延后重试，不缩短催办间隔。

---

## 10. 失败与降级

### 卡片发送失败

- Delivery 进入正常重试；
- 不增加“用户已收到”的 notify_count，或分别记录 generated_count/sent_count；
- 达到最大失败次数后显示渠道故障，但 Reminder 仍可由后台完成。

### 点击回调已处理，卡片更新失败

- Reminder 保持 completed；
- InteractionEvent 标记 `processed_with_display_error`；
- 可发送普通文本确认；
- 后台提供“重新同步卡片状态”不是必要功能。

### 企业微信回调短暂不可用

- 企业微信可能重试回调；
- callback handler 必须快速返回；
- 回调先落库再异步处理；
- 唯一索引阻止重复执行动作。

### response_code 已过期或已使用

- 不影响完成任务；
- 不再重试更新卡片；
- 必要时发送一条普通文本确认。

---

## 11. API 建议

创建持续提醒：

```http
POST /api/v1/reminders
```

```json
{
  "title": "关闭燃气阀",
  "content": "确认燃气阀已关闭后点击完成",
  "schedule": {
    "type": "once",
    "start_at": "2026-07-14T15:00:00+08:00"
  },
  "acknowledgement": {
    "required": true,
    "policy": "any",
    "repeat_interval_seconds": 300,
    "max_reminders": 12,
    "stop_at": "2026-07-15T15:00:00+08:00",
    "actions": ["complete"]
  },
  "recipients": ["person_vigoss"]
}
```

后台完成：

```http
POST /api/v1/reminders/{reminder_id}/complete
Idempotency-Key: ...
```

企业微信按钮不调用公开 HTTP API，而是通过加密回调进入：

```http
POST /api/v1/channels/wecom/callback
```

---

## 12. 管理后台

提醒详情页至少显示：

- 当前状态；
- 接收人状态；
- 提醒间隔；
- 已发送次数；
- 下一次提醒时间；
- 最大次数和截止时间；
- 最近卡片投递；
- 点击完成记录；
- 已取消的待发送 Delivery；
- 卡片更新是否成功；
- 手工暂停、恢复、完成和取消按钮。

提醒时间线示例：

```text
15:00 创建提醒
15:00 第 1 次卡片发送成功
15:05 第 2 次卡片发送成功
15:10 第 3 次卡片发送成功
15:12 Vigoss 点击“已完成”
15:12 Reminder completed
15:12 取消 1 条 pending Delivery
15:12 卡片更新成功
```

---

## 13. 测试要求

### 单元测试

- action token 生成和哈希；
- EventKey 解析；
- 发送者权限；
- any/all/each 状态计算；
- 最大次数和 stop_at；
- 完成操作幂等；
- 旧 token；
- response_code 清理。

### 集成测试

- Reminder Worker 生成交互卡片；
- 模拟 template_card_event 回调；
- 点击后不再生成 Delivery；
- pending/retry_wait Delivery 被取消；
- 重复回调只处理一次；
- 未授权 UserID 不能完成；
- 回调处理成功但卡片更新失败；
- 服务重启后继续调度；
- 点击与 Worker 并发竞态。

### 端到端手工验收

1. 创建每 5 分钟提醒；
2. 收到第 1 张带按钮卡片；
3. 不点击，等待第 2 张；
4. 点击任意一张卡片的“已完成”；
5. 卡片显示完成状态；
6. 等待至少两个提醒周期，确认没有新卡片；
7. 再点击旧卡片，后台显示幂等处理；
8. 重复测试服务重启和企业微信短暂失败。

---

## 14. 实现顺序

建议拆为以下 PR：

1. `feat: add reminder acknowledgement domain model`
2. `feat: add wecom interactive template card sender`
3. `feat: handle wecom template card callback events`
4. `feat: stop continuous reminders on acknowledgement`
5. `feat: update completed template cards`
6. `feat: add continuous reminder management UI`
7. `test: cover interactive reminder races and recovery`

其中第 2～4 个 PR 必须在同一个发布版本中完成。只发送按钮但无法可靠接收回调，不应视为可用功能。

---

## 15. MVP 验收定义

该场景只有同时满足以下条件才算完成：

- 每次催办都是普通文字、图文或图片消息，并包含完整的底部菜单提示；
- 卡片存在“已完成”按钮；
- 点击事件经过企业微信签名验证和解密；
- 只允许合法接收人完成；
- 点击后数据库状态原子更新；
- 点击后不再生成后续催办；
- 待发送投递被取消；
- 重复点击和重复回调幂等；
- 点击历史卡片也能安全完成或返回已完成；
- 当前卡片能更新为完成状态，失败时有降级反馈；
- 服务重启后不会恢复已经完成的提醒；
- 管理后台可以审计完整时间线。
