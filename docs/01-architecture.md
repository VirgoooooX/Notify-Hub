# 系统架构

## 1. 架构结论

Notify Hub 第一阶段采用**模块化单体**：

- 一个 Git 仓库；
- 一个后端进程；
- 一个前端构建产物；
- 一个数据库；
- 一个 Docker 镜像；
- 一个企业微信“系统通知”应用；
- 多个受控的内置或私有监控插件。

模块化单体不是把所有逻辑写在一起。核心、渠道、插件、提醒、会话和后台任务必须通过明确接口通信。

## 2. 顶层组件

```text
外部系统 / API Client              企业微信成员
          │                              │
          │ POST /api/v1/events          │ 文字/语音/菜单
          ▼                              ▼
┌──────────────────────────────────────────────────┐
│                  Notify Hub API                  │
│  管理员认证 | API Key 认证 | 回调验签 | 限流     │
└───────────────────────┬──────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────┐
│                Application Services              │
│ EventService | ReminderService | PluginService   │
│ ConversationService | DeliveryService            │
└──────────────┬──────────────────────┬────────────┘
               │                      │
               ▼                      ▼
┌────────────────────────┐   ┌─────────────────────┐
│ Plugin Runtime         │   │ Delivery Worker     │
│ Scheduler              │   │ Retry / Claim / Send│
│ Plugin Context         │   └──────────┬──────────┘
└──────────┬─────────────┘              │
           │ emit_event                  ▼
           └───────────────────> WeCom Channel Adapter
                                      │
                                      ▼
                               企业微信应用 API
```

## 3. 推荐目录结构

```text
notify-hub/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── admin_auth.py
│   │   │   ├── events.py
│   │   │   ├── notifications.py
│   │   │   ├── reminders.py
│   │   │   ├── plugins.py
│   │   │   ├── api_clients.py
│   │   │   └── wecom_callback.py
│   │   ├── application/
│   │   │   ├── event_service.py
│   │   │   ├── delivery_service.py
│   │   │   ├── reminder_service.py
│   │   │   ├── conversation_service.py
│   │   │   └── plugin_service.py
│   │   ├── domain/
│   │   │   ├── events.py
│   │   │   ├── notifications.py
│   │   │   ├── reminders.py
│   │   │   ├── plugins.py
│   │   │   └── identities.py
│   │   ├── infrastructure/
│   │   │   ├── database/
│   │   │   ├── security/
│   │   │   ├── scheduler/
│   │   │   ├── logging/
│   │   │   └── http/
│   │   ├── channels/
│   │   │   └── wecom/
│   │   │       ├── client.py
│   │   │       ├── adapter.py
│   │   │       ├── callback.py
│   │   │       ├── crypto.py
│   │   │       └── media.py
│   │   ├── plugin_runtime/
│   │   │   ├── base.py
│   │   │   ├── context.py
│   │   │   ├── registry.py
│   │   │   ├── runner.py
│   │   │   └── manifest.py
│   │   └── workers/
│   │       ├── delivery_worker.py
│   │       ├── reminder_worker.py
│   │       └── plugin_worker.py
│   ├── migrations/
│   └── tests/
├── frontend/
│   ├── src/
│   └── tests/
├── plugins/
│   ├── builtin/
│   │   └── codex_x_monitor/
│   └── private/
├── deploy/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── nginx.example.conf
├── docs/
├── .env.example
├── pyproject.toml
└── README.md
```

第一阶段可适当简化目录，但不得把插件实现放入 `channels/wecom`，也不得在路由函数中直接写投递业务。

## 4. 模块职责

### 4.1 API 层

API 层只负责：

- 解析输入；
- 身份认证；
- 参数校验；
- 调用 Application Service；
- 映射 HTTP 状态码；
- 返回稳定响应模型。

API 层不得：

- 直接调用企业微信；
- 直接修改插件状态；
- 直接执行 SQL；
- 直接启动定时任务。

### 4.2 EventService

负责统一接收：

- 外部 API 事件；
- 插件事件；
- 提醒触发事件；
- 系统内部事件。

核心步骤：

1. 规范化来源；
2. 校验权限；
3. 生成或校验 `event_key`；
4. 幂等写入；
5. 计算接收人和投递策略；
6. 创建 Notification 与 Delivery；
7. 返回已接受结果。

### 4.3 DeliveryService 与 Worker

DeliveryService 创建投递记录，Worker 负责实际发送。

必须采用“数据库队列”而不是在 API 请求内直接完成全部投递：

```text
pending -> processing -> succeeded
                    └-> retry_wait -> pending
                    └-> dead
```

第一阶段 SQLite 单实例运行时，可以通过事务和状态更新时间实现安全 claim。未来迁移 PostgreSQL 后再使用行级锁和 `SKIP LOCKED`。

### 4.4 ReminderService

负责提醒的创建、确认、暂停、恢复、取消、触发和下一次时间计算。提醒触发后不直接发送，而是产生内部事件。

### 4.5 ConversationService

负责企业微信输入与提醒草稿之间的状态机：

```text
idle
  -> awaiting_confirmation
  -> awaiting_time
  -> awaiting_recipient
  -> completed / cancelled / expired
```

大模型或自然语言解析器只能返回结构化草稿。正式提醒必须经过业务校验和用户确认。

### 4.6 Plugin Runtime

Plugin Runtime 负责：

- 发现受信任插件；
- 解析 Manifest；
- 校验配置；
- 提供受控 Context；
- 定时执行；
- 超时控制；
- 异常隔离；
- 保存状态和运行历史；
- 连续失败熔断。

插件不得拿到数据库 Session、企业微信 Secret 或管理员 Token。

### 4.7 WeCom Channel Adapter

企业微信适配层对核心暴露统一接口：

```python
class NotificationChannel:
    async def send(self, message: ChannelMessage) -> ChannelResult: ...
    async def test(self) -> ChannelResult: ...
```

它负责：

- Access Token 缓存和刷新；
- 代理地址；
- 文本、图文、图片、语音消息转换；
- UserID 编码；
- 媒体上传与下载；
- 企业微信错误码映射；
- 网络超时和可重试错误分类。

核心领域模型不得依赖 `touser`、`agentid`、`media_id` 等企业微信字段。

## 5. 事件流

### 5.1 插件事件

```text
Scheduler
  -> PluginRunner.run(plugin)
  -> plugin.run(context)
  -> context.emit_event(draft)
  -> EventService.accept()
  -> Event + Notification + Delivery 落库
  -> plugin state 更新游标
  -> API/调度执行结束
  -> DeliveryWorker claim
  -> WeComAdapter.send()
  -> DeliveryAttempt 记录
```

### 5.2 外部 API 事件

```text
Client
  -> API Key middleware
  -> EventService.accept()
  -> 202 Accepted
  -> DeliveryWorker
  -> 企业微信
```

### 5.3 企业微信对话提醒

```text
WeCom Callback
  -> 验签与解密
  -> IncomingMessage 规范化
  -> ConversationService
  -> ReminderDraft
  -> 用户确认
  -> ReminderService.create()
  -> 到期产生 Internal Event
  -> DeliveryWorker
```

## 6. 插件运行模型

### 第一阶段

- 只加载仓库内置和管理员手动放入指定目录的可信插件；
- 插件在宿主进程中运行；
- 异步插件使用超时包装；
- 同步或阻塞插件放在线程池；
- 每个插件限制并发数，默认 1；
- 连续失败达到阈值后自动暂停调度，但保留“立即运行”；
- 插件运行不能阻塞 API 事件循环。

### 后续阶段

当需要执行不可信插件或任意脚本时，新增独立 Plugin Worker：

- 子进程或独立容器；
- 受控文件系统；
- 环境变量白名单；
- CPU、内存和超时限制；
- 通过本地 RPC 与核心通信。

插件契约应保持不变，避免迁移时重写所有插件。

## 7. 调度和恢复

所有调度对象都必须有数据库事实来源：

- 插件调度配置；
- Reminder 的 `next_run_at`；
- Delivery 的 `next_attempt_at`。

进程启动时：

1. 执行数据库迁移；
2. 加载平台配置；
3. 注册渠道；
4. 加载插件 Manifest；
5. 恢复插件调度；
6. 扫描过期未完成的 processing 任务并回收；
7. 启动 Delivery、Reminder 和 Plugin worker；
8. 最后对外报告 Ready。

不得只依赖内存中的定时任务对象。

## 8. 数据库与扩展路径

MVP 使用 SQLite，限制：

- 只运行一个应用实例；
- 一个进程拥有调度器；
- 写入操作保持短事务；
- 开启 WAL；
- 配置 busy timeout；
- 不在事务中执行网络请求。

迁移到 PostgreSQL 的触发条件：

- 需要多个 API/Worker 实例；
- 高频事件导致 SQLite 写锁竞争；
- 需要更强的任务 claim；
- 需要复杂报表或更长历史保留。

## 9. 可观测性

日志必须结构化并至少包含：

- `request_id`；
- `event_id`；
- `notification_id`；
- `delivery_id`；
- `plugin_id`；
- `plugin_run_id`；
- `client_id`；
- 错误类别，而不是 Secret 或完整敏感请求。

健康检查分为：

- `/health/live`：进程是否存活；
- `/health/ready`：数据库、迁移、核心 worker 是否就绪；
- 企业微信连通性测试：管理员主动触发，不作为每次 readiness 的硬依赖。

## 10. 架构约束

以下约束应通过代码审查和测试执行：

1. 插件禁止导入 `channels.wecom`；
2. API 路由禁止直接调用渠道 Adapter；
3. 网络请求禁止放在数据库事务内；
4. 所有待投递消息必须有持久化记录；
5. 所有外部事件必须有幂等键；
6. Secret 不得进入插件普通配置 JSON；
7. 插件状态只能通过 `PluginContext` 读写；
8. 服务重启不能丢失 Reminder、Delivery 或插件采集游标；
9. `@all` 必须经过显式权限检查；
10. 第一阶段禁止动态 `pip install` 未审核依赖。
