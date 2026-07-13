# 开发路线图

## 1. 总体策略

开发顺序遵循：

```text
基础工程
  -> 事件与数据库队列
  -> 企业微信出站通知
  -> 插件宿主
  -> Codex X Monitor
  -> 管理后台
  -> 企业微信入站对话
  -> 提醒与持续催办
  -> 图片/语音与高级能力
```

不要先开发自然语言、TTS 或任意脚本执行。第一条完整闭环应是：

```text
Codex X 插件发现推文
  -> Event 落库
  -> Notification/Delivery 创建
  -> 企业微信指定成员收到文本
  -> 后台可查看完整投递记录
```

## 2. 分支与提交策略

推荐：

- `main`：始终可部署；
- 功能分支：`feat/<scope>`；
- 修复分支：`fix/<scope>`；
- 文档分支：`docs/<scope>`；
- 每个 PR 只完成一个可验证阶段或垂直切片；
- 数据库迁移必须与模型改动位于同一 PR；
- 不提交真实 Secret、Cookie、Token 和数据库文件。

推荐 PR 顺序见各阶段。

---

# Phase 0：工程初始化

## 目标

建立可运行、可测试、可迁移、可容器化的空平台。

## 任务

### 后端

- 初始化 Python 项目与依赖管理；
- 建立 FastAPI 应用工厂；
- 建立配置模型；
- 建立结构化日志；
- 建立 SQLAlchemy Session 管理；
- 初始化 Alembic；
- 实现 `/health/live` 和 `/health/ready`；
- 建立统一异常和响应模型；
- 生成 OpenAPI；
- 建立 pytest 和异步测试基础设施。

### 前端

- 初始化 Vue 3 + TypeScript + Vite；
- 建立路由、状态管理和 API Client；
- 提供登录页和基础布局占位；
- 配置单元测试与构建检查。

### 部署

- 多阶段 Dockerfile；
- docker-compose 示例；
- `.env.example`；
- 数据和日志卷；
- 非 root 用户运行；
- 容器健康检查。

## 验收

- `docker compose up` 可启动；
- `/health/live` 返回 200；
- `/health/ready` 可识别数据库不可用；
- Alembic 能从空数据库升级到最新；
- 后端和前端测试命令可运行；
- CI 检查格式、类型、测试和构建。

## 建议 PR

1. `chore: initialize backend and development tooling`
2. `chore: initialize frontend and container build`

---

# Phase 1：管理员认证与基础设置

## 目标

安全进入管理后台，并能保存非敏感平台设置。

## 任务

- 单管理员初始化流程；
- 密码哈希；
- 登录、登出、刷新会话；
- CSRF/同源策略；
- 登录限流；
- 管理员操作审计；
- 配置页面骨架；
- Secret 配置抽象；
- 主加密密钥检查；
- 首次启动未配置管理员时的受限初始化模式。

## 验收

- 未认证无法访问管理员 API；
- API Client Key 不能用于后台登录；
- 密码和 Secret 不明文入库；
- 登录失败被限流；
- 审计日志记录登录和配置变更。

---

# Phase 2：事件、通知和数据库投递队列

## 目标

先完成与渠道无关的可靠消息核心。

## 任务

### 数据模型

- Event；
- Notification；
- Delivery；
- DeliveryAttempt；
- Person；
- WeComIdentity；
- ApiClient；
- AuditLog。

### Event API

- 创建和管理 API Client；
- API Key 哈希与一次性展示；
- `POST /api/v1/events`；
- Client 权限；
- Client 限流；
- `event_key` 幂等；
- 事件路由为 Notification/Delivery；
- `202 Accepted`。

### Worker

- Delivery claim；
- processing 超时回收；
- retry_wait；
- dead 状态；
- 退避计算；
- Worker 心跳。

先提供一个 FakeChannel，测试完整队列，不急于接企业微信。

## 验收

- 重复 Event 只创建一次投递；
- API 返回 202 后重启，任务仍存在；
- Worker 崩溃后 processing 可回收；
- 可重试错误进入 retry_wait；
- 不可重试错误进入 dead；
- 单个接收人对应独立 Delivery；
- `@all` 权限可拒绝。

## 建议 PR

1. `feat: add event and delivery domain models`
2. `feat: add API clients and event ingestion`
3. `feat: add persistent delivery worker`

---

# Phase 3：企业微信出站通知

## 目标

把数据库队列可靠地投递到独立企业微信“系统通知”应用。

## 任务

- WeCom 配置与 Secret；
- Token 缓存；
- 并发刷新锁；
- 代理支持；
- 文本发送；
- 指定 UserID；
- 多 UserID；
- 图文消息；
- 消息长度处理；
- 错误码映射；
- 临时错误分类；
- 管理员连通性测试；
- Person 与 WeComIdentity 管理页面；
- 测试通知页面。

## 验收

- 可向指定企业微信成员发送；
- 无 recipient 时不隐式广播；
- 显式广播需要权限；
- Token 过期可刷新；
- 代理不可用时事件不丢失；
- 企业微信永久参数错误不会重复五次；
- 发送结果和错误可在后台查看；
- 日志中没有 Secret 和 Token。

## 第一条端到端手工验收

```bash
curl -X POST https://notify.example.com/api/v1/events \
  -H 'X-API-Key: nfy_...' \
  -H 'Content-Type: application/json' \
  -d '{
    "event_type": "system.manual_test",
    "event_key": "manual-test-001",
    "title": "Notify Hub 测试",
    "content": "事件、队列与企业微信投递链路正常",
    "recipients": ["person_vigoss"]
  }'
```

---

# Phase 4：插件宿主

## 目标

建立可信插件的统一生命周期、调度、状态和配置能力。

## 任务

- Manifest 解析；
- Plugin Registry；
- Plugin Base API；
- PluginContext；
- PluginState；
- PluginRun；
- 配置 Schema 校验；
- SecretRef；
- interval/cron 调度；
- 立即运行；
- 最大并发；
- 超时；
- 取消；
- 连续失败计数；
- 熔断与恢复；
- 插件日志上下文；
- Fake Plugin 集成测试。

## 验收

- 插件不能直接拿到数据库 Session；
- 插件通过 Context 发事件；
- emit 成功/duplicate 后才能更新游标；
- 插件异常不影响 API；
- 插件超时可终止等待并记录 timed_out；
- 重启后恢复插件调度；
- 同一插件默认不重叠运行；
- 配置不合法时保留最后有效版本。

## 建议 PR

1. `feat: add plugin manifest and registry`
2. `feat: add plugin context and state storage`
3. `feat: add persistent plugin scheduler and run history`

---

# Phase 5：Codex X Monitor 插件

## 目标

完成第一个真实插件，验证整套平台设计。

## 任务

- 插件 Manifest；
- RSS/Atom 数据源；
- 可选 X API 数据源；
- 账号配置；
- 关键词和正则规则；
- 首次 baseline；
- 推文 ID 游标；
- 新推文排序；
- 稳定 `event_key`；
- 通知内容格式；
- 接收人配置；
- 来源超时和错误；
- 固定 fixture 测试；
- 从旧独立脚本迁移关键逻辑；
- 插件 README。

## 验收

- 首次启用默认不通知历史推文；
- 新的匹配推文产生一条事件；
- 不匹配推文只推进已扫描游标；
- 同一推文重复抓取不重复通知；
- Notify Hub 接受事件失败时不推进游标；
- 多条积压推文按时间顺序处理；
- RSS 源异常显示插件 degraded；
- API/RSS 切换不重复发送旧推文；
- 企业微信消息包含作者、摘要和原文链接。

---

# Phase 6：管理后台 MVP

## 目标

无需进入数据库或日志文件即可完成日常管理。

## 页面

### 仪表盘

- 今日事件数；
- 成功/失败投递；
- 待重试数量；
- 失败插件；
- 最近系统错误。

### 插件

- 插件列表；
- 启用/禁用；
- 配置表单；
- Secret 状态；
- 调度；
- 立即运行；
- 最近运行；
- 最近错误；
- 状态查看。

### 通知

- Event 列表；
- Notification 详情；
- 每个 Delivery 时间线；
- dead 手工重试；
- 条件过滤。

### 接收人

- Person；
- 企业微信 UserID；
- 默认接收人；
- 广播权限说明。

### API Client

- 创建；
- 一次性显示 Key；
- 权限；
- 限流；
- 轮换；
- 吊销。

### 设置

- 企业微信配置状态；
- 发送测试；
- 时区；
- 数据保留；
- 系统版本。

## 验收

- 后台所有状态与 API 一致；
- Secret 永不回显；
- 危险操作二次确认；
- 列表支持分页和错误状态过滤；
- 立即运行不会重复点击创建大量任务；
- 移动端浏览可用，但不要求完整移动端优化。

---

# Phase 7：企业微信入站与文字提醒

## 目标

可以在企业微信里用文字创建和管理提醒。

## 任务

- 回调 URL 验证；
- 消息验签与解密；
- IncomingMessage；
- 重复回调去重；
- WeCom UserID 自动识别；
- 管理员白名单；
- ConversationSession；
- 命令路由；
- 提醒草稿；
- 时间解析；
- 用户确认；
- 单次 Reminder；
- Reminder worker；
- `/今天`、`/提醒`、`/取消`、`/完成`、`/稍后`、`/帮助`。

## 验收

- “明天下午三点提醒我交电费”产生草稿；
- 用户确认后才创建；
- 歧义时间会追问；
- 普通成员不能创建给无权限人员的提醒；
- 回调重试不会创建两条；
- 服务重启后提醒仍触发；
- 会话过期后不会把普通文字误当确认。

第一版自然语言解析可以使用规则和日期解析库，LLM 作为可选增强，不作为硬依赖。

---

# Phase 8：周期提醒与持续催办

## 目标

支持重复提醒和“直到确认”的任务。

## 任务

- RRULE；
- next_run_at 计算；
- 暂停/恢复；
- require_ack；
- any/all/each 确认策略；
- 催办间隔；
- 最大次数；
- 截止时间；
- 企业微信确认命令；
- 管理后台状态和时间线；
- 安全上限。

## 默认保护

- 最短催办间隔：5 分钟；
- 默认最大催办次数：12；
- 默认最长持续时间：24 小时；
- 广播持续催办默认禁止；
- 无限催办必须管理员二次确认。

## 验收

- 未确认按规则重复；
- 确认后不再生成 Delivery；
- 任一确认/全部确认策略正确；
- 暂停后不触发；
- 时区和夏令时边界有测试；
- 重启后 next_run_at 正确恢复。

---

# Phase 9：语音输入、图片与语音投递

## 目标

完成多媒体交互，但不影响文本可靠性。

## 任务

### 语音输入

- 企业微信 Recognition 优先；
- 媒体下载；
- ASR 接口抽象；
- SenseVoice 或其他本地 ASR Adapter；
- 音频临时文件清理；
- 识别确认。

### 图片

- 图片下载与校验；
- 媒体存储；
- 图文通知；
- 独立图片消息；
- 过期清理。

### 语音通知

- TTS Adapter；
- 音频转码；
- 临时素材上传；
- voice 消息；
- 失败降级文本；
- 严重级别权限。

## 验收

- 语音识别失败不误建提醒；
- 媒体文件按策略删除；
- 不支持格式有明确错误；
- TTS 故障时文本仍可发送；
- 大文件、恶意 MIME 和超限媒体被拒绝。

---

# Phase 10：通用监控与脚本适配器

## 目标

减少为小需求重复开发插件。

可选内置插件：

- HTTP 健康检查；
- SSL 证书到期；
- 公网 IP 变化；
- Docker 容器状态；
- 通用 RSS 关键词监控；
- 受控脚本适配器。

受控脚本适配器最后开发，并遵守：

- 不允许网页上传任意可执行文件；
- 只运行挂载目录；
- argv 白名单；
- 无 shell；
- 超时和输出限制；
- 环境变量白名单；
- 子进程权限隔离。

---

# 3. 每阶段完成定义

每个阶段只有同时满足以下条件才算完成：

- 功能代码；
- 数据库迁移；
- 单元测试；
- 集成测试；
- API/OpenAPI 更新；
- 配置示例；
- 运维说明；
- 错误和恢复路径；
- 不泄露 Secret；
- PR 描述中列出手工验收结果。

# 4. MVP 发布定义

`v0.1.0` 建议包含 Phase 0～6：

- 可部署平台；
- 可靠事件 API；
- 企业微信文本/图文通知；
- 指定 UserID；
- 插件系统；
- Codex X Monitor；
- 管理后台；
- 完整通知历史和重试。

`v0.2.0` 包含文字创建提醒和周期/持续催办。

`v0.3.0` 包含语音输入、图片和语音投递。

# 5. 当前第一开发任务

从本规划进入编码时，第一项应为：

```text
Phase 0 / PR 1：初始化后端、数据库迁移、配置、日志、健康检查和测试框架
```

不要直接从 Codex 插件开始写，因为插件所依赖的事件、状态和调度契约尚未实现。
