# API 契约

## 1. 通用约定

基础路径：

```text
/api/v1
```

内容类型：

```text
application/json
```

外部系统认证：

```http
X-API-Key: nfy_xxxxxxxxxxxxxxxxx
```

管理员后台认证使用独立会话或 Bearer Token，不与外部 API Key 混用。

所有响应包含请求追踪 ID：

```http
X-Request-ID: req_01...
```

错误响应：

```json
{
  "error": {
    "code": "invalid_request",
    "message": "event_key 不能为空",
    "details": {}
  },
  "request_id": "req_01..."
}
```

## 2. 状态码约定

- `200 OK`：同步查询或幂等重复请求已确认；
- `201 Created`：管理资源创建成功；
- `202 Accepted`：事件已持久化并等待异步投递；
- `204 No Content`：删除或状态变更成功；
- `400 Bad Request`：参数格式错误；
- `401 Unauthorized`：未提供或无法识别身份；
- `403 Forbidden`：身份有效但无权限；
- `404 Not Found`：资源不存在；
- `409 Conflict`：状态冲突；
- `422 Unprocessable Entity`：字段通过 JSON 解析但业务校验失败；
- `429 Too Many Requests`：限流；
- `500 Internal Server Error`：平台内部错误；
- `503 Service Unavailable`：数据库或核心 Worker 未就绪。

## 3. 提交事件

```http
POST /api/v1/events
X-API-Key: ...
Idempotency-Key: optional-compatible-header
```

请求：

```json
{
  "event_type": "codex.usage_reset",
  "event_key": "x-post-2061106703446450392",
  "title": "Codex 用量已重置",
  "content": "@thsottiaux 发布了新的 Codex 用量重置通知",
  "level": "info",
  "occurred_at": "2026-07-13T12:00:00Z",
  "url": "https://x.com/thsottiaux/status/2061106703446450392",
  "image_url": null,
  "recipients": ["person_vigoss"],
  "message_type": "text",
  "require_ack": false,
  "payload": {
    "author": "thsottiaux",
    "post_id": "2061106703446450392"
  }
}
```

约束：

- `event_type`：1～100 字符；
- `event_key`：1～200 字符，来源内稳定；
- `title`：1～200 字符；
- `content`：允许为空但不能与 title 同时为空；
- `payload`：序列化后有大小限制；
- `recipients` 只能包含该 API Client 有权使用的目标；
- `@all` 广播使用独立标志或特殊目标，并要求广播权限；
- Header `Idempotency-Key` 存在时，应与 body `event_key` 一致或由服务端明确映射。

首次接受：

```json
{
  "data": {
    "event_id": "evt_01...",
    "status": "accepted",
    "duplicate": false
  },
  "request_id": "req_01..."
}
```

重复提交：

```json
{
  "data": {
    "event_id": "evt_01...",
    "status": "accepted",
    "duplicate": true
  },
  "request_id": "req_01..."
}
```

重复提交不得创建新的 Notification 或 Delivery。

## 4. 直接发送通知

管理后台和受信任 Client 可使用：

```http
POST /api/v1/notifications
```

该接口仍必须先创建持久化 Notification 和 Delivery，不应绕过队列同步调用企业微信。

请求：

```json
{
  "title": "测试通知",
  "content": "这是一条测试消息",
  "message_type": "text",
  "recipients": ["person_vigoss"],
  "priority": "normal",
  "url": null,
  "image_url": null,
  "require_ack": false
}
```

返回 `202 Accepted`。

## 5. 查询事件

```http
GET /api/v1/admin/events?source_id=codex_x_monitor&status=routed&page=1&page_size=50
```

管理员接口支持：

- 来源；
- 事件类型；
- 时间范围；
- 状态；
- 关键字；
- 是否重复；
- 分页。

## 6. 查询通知和投递

```http
GET /api/v1/admin/notifications
GET /api/v1/admin/notifications/{notification_id}
GET /api/v1/admin/deliveries/{delivery_id}/attempts
```

详情应展示：

- 原始事件；
- 通知内容；
- 每个收件人的状态；
- 尝试次数；
- 企业微信归一化错误；
- 时间线。

不返回 Secret、Access Token 或完整供应商响应。

## 7. 重试或取消投递

```http
POST /api/v1/admin/deliveries/{delivery_id}/retry
POST /api/v1/admin/deliveries/{delivery_id}/cancel
```

规则：

- succeeded 不允许原地 retry；
- dead 可重置为 pending，并创建新的 Attempt 编号；
- processing 只能在 claim 过期后由系统回收；
- retry 操作进入审计日志。

## 8. API Client 管理

```http
POST   /api/v1/admin/api-clients
GET    /api/v1/admin/api-clients
PATCH  /api/v1/admin/api-clients/{id}
POST   /api/v1/admin/api-clients/{id}/rotate-key
POST   /api/v1/admin/api-clients/{id}/revoke
```

创建响应只显示一次明文 Key：

```json
{
  "data": {
    "id": "client_codex_external",
    "name": "Codex external monitor",
    "api_key": "nfy_...",
    "key_prefix": "nfy_ab12"
  }
}
```

前端必须提示用户立即保存，刷新后无法再次查看。

## 9. 插件管理

```http
GET  /api/v1/admin/plugins
GET  /api/v1/admin/plugins/{plugin_id}
PUT  /api/v1/admin/plugins/{plugin_id}/config
POST /api/v1/admin/plugins/{plugin_id}/enable
POST /api/v1/admin/plugins/{plugin_id}/disable
POST /api/v1/admin/plugins/{plugin_id}/run
GET  /api/v1/admin/plugins/{plugin_id}/runs
GET  /api/v1/admin/plugins/{plugin_id}/state
POST /api/v1/admin/plugins/{plugin_id}/test
```

`run` 返回：

```json
{
  "data": {
    "run_id": "prun_01...",
    "status": "queued"
  }
}
```

立即运行也应进入统一 PluginRun 队列，不在 HTTP 请求中等待外部数据源完成。

配置更新：

- 先按插件 Schema 校验；
- Secret 单独提交；
- 保存成功后重新计算调度；
- 无效配置不得覆盖最后一份有效配置；
- 更新进入审计日志。

## 10. Plugin Secret 管理

```http
PUT    /api/v1/admin/plugins/{plugin_id}/secrets/{name}
DELETE /api/v1/admin/plugins/{plugin_id}/secrets/{name}
GET    /api/v1/admin/plugins/{plugin_id}/secrets
```

GET 只返回：

```json
{
  "name": "x_api_bearer_token",
  "configured": true,
  "updated_at": "..."
}
```

不得返回密文或明文。

## 11. 提醒 API

```http
POST   /api/v1/admin/reminders
GET    /api/v1/admin/reminders
GET    /api/v1/admin/reminders/{id}
PATCH  /api/v1/admin/reminders/{id}
POST   /api/v1/admin/reminders/{id}/pause
POST   /api/v1/admin/reminders/{id}/resume
POST   /api/v1/admin/reminders/{id}/complete
POST   /api/v1/admin/reminders/{id}/cancel
POST   /api/v1/admin/reminders/{id}/snooze
```

单次提醒：

```json
{
  "title": "交电费",
  "content": "记得完成缴费",
  "schedule": {
    "type": "once",
    "at": "2026-07-14T15:00:00+08:00"
  },
  "recipients": ["person_vigoss"],
  "require_ack": true,
  "repeat": {
    "interval_seconds": 600,
    "max_attempts": 12,
    "stop_at": "2026-07-14T23:00:00+08:00"
  }
}
```

周期提醒：

```json
{
  "title": "提交周报",
  "schedule": {
    "type": "recurring",
    "rrule": "FREQ=WEEKLY;BYDAY=MO;BYHOUR=9;BYMINUTE=0",
    "timezone": "Asia/Shanghai"
  },
  "recipients": ["person_vigoss"]
}
```

## 12. 接收人管理

```http
GET    /api/v1/admin/people
POST   /api/v1/admin/people
PATCH  /api/v1/admin/people/{id}
POST   /api/v1/admin/people/{id}/wecom-identities
DELETE /api/v1/admin/people/{id}/wecom-identities/{identity_id}
```

第一阶段允许手工维护企业微信 UserID，并可在收到合法回调后提示管理员关联身份。

## 13. 企业微信回调

```http
GET  /api/v1/channels/wecom/callback
POST /api/v1/channels/wecom/callback
```

GET 用于 URL 验证，POST 用于接收消息和事件。

要求：

- 验证签名；
- 解密消息；
- 快速响应；
- 原始消息持久化后异步处理；
- 回调重试幂等；
- 日志不记录完整签名参数、密文或语音内容。

## 14. 企业微信测试

```http
POST /api/v1/admin/channels/wecom/test
```

请求：

```json
{
  "recipient_id": "person_vigoss",
  "message_type": "text"
}
```

测试也应写入正常发送记录，但标记 `source_type=system`、`event_type=system.channel_test`。

## 15. 健康检查

```http
GET /health/live
GET /health/ready
```

`live` 不访问外部服务，仅证明进程事件循环可响应。

`ready` 检查：

- 数据库连接；
- 迁移版本；
- 核心 Worker 心跳；
- 插件注册表初始化；
- 关键配置完整性。

企业微信 API 暂时不可用不应让整个服务从负载均衡中摘除，否则无法接收并排队新事件。

## 16. 限流建议

- API Client：按 Client 独立令牌桶；
- 登录：按 IP 和用户名限制；
- 企业微信回调：不使用普通 API Key 限流，但启用签名验证和合理并发保护；
- 管理员立即运行插件：每插件冷却时间；
- 广播通知：额外限制；
- 高优先级语音通知：额外限制。

## 17. 版本演进

破坏性改动只进入 `/api/v2`。在 v1 中：

- 可以新增可选字段；
- 不随意改变字段语义；
- 枚举新增值时客户端应按未知值兼容；
- 废弃字段先标记，再在下一个 major 删除；
- OpenAPI 文档作为机器可读契约；
- 关键请求与响应必须有契约测试。
