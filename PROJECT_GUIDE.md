# Notify Hub 项目开发总纲

本文是 Notify Hub 当前开发约束的统一入口。代码、数据库迁移、OpenAPI 和测试是实现事实源；本文只保存跨模块、长期有效的规则。普通 Bug、文案和局部 UI 调整不要求同步修改本文。

历史设计资料已归档到 `docs/archive/design-phase/`，仅用于追溯，不代表当前实现。重要架构变化在 `docs/DECISIONS.md` 新增 ADR；部署与恢复操作见 `docs/operations.md`。

## 1. 产品边界

Notify Hub 是面向个人、家庭和小型自托管环境的企业微信通知与提醒平台，核心能力包括：

- 可靠事件接收、幂等和数据库投递队列；
- 企业微信指定成员、受控广播、回调和菜单操作；
- Once、Interval、Cron、周期和持续催办提醒；
- 受控可信插件、Codex X Monitor 和 Fabrizio HWG Monitor；
- 图片、语音和签名媒体访问；
- AI Provider、Profile、预算、缓存和调用审计；
- 管理后台与企业微信成员移动页面。

当前不做多租户商业 SaaS、通用工作流平台、网页上传任意脚本、不可信进程内插件、LLM 直接操作数据库或渠道，以及无明确需求的 Redis、RabbitMQ 和微服务拆分。

## 2. 架构与依赖

项目采用单仓库、单后端、单前端、单数据库和单镜像的模块化单体。SQLite 是当前可恢复任务的事实来源，只允许一个应用实例写入同一数据库。

推荐依赖方向：

```text
api -> application -> domain
                    -> infrastructure interfaces
channels -> domain/application interfaces
plugin_runtime -> application interfaces
plugins -> PluginContext only
workers -> application services
web -> HTTP API only
```

禁止：

- `plugin -> channels.wecom`；
- `plugin -> infrastructure.database`；
- `api -> channels.wecom.client`；
- `web -> database`；
- 在模块导入时启动后台任务。

应用启动、Worker 恢复和关闭统一通过 FastAPI 生命周期管理。

## 3. 可靠消息核心

核心模型分为：

- `Event`：发生了什么；
- `Notification`：准备发送什么；
- `Delivery`：发给谁、投递状态如何；
- `DeliveryAttempt`：每次实际尝试的审计记录。

外部 Event API 必须在 Event、Notification 和 Delivery 成功持久化后返回 `202 Accepted`。所有外部事件都必须提供来源稳定的 `event_key`，数据库唯一约束是最终幂等防线。

投递状态通过数据库队列推进：

```text
pending -> processing -> succeeded
                    -> retry_wait -> pending
                    -> dead
```

网络发送必须在数据库事务外。Worker 使用短事务 claim、租约、心跳、退避和过期回收。已成功的 Delivery 不回退；重新发送创建新的 Notification/Delivery。

## 4. 数据库与时间

- 使用 SQLAlchemy、Alembic 和异步 Session；模型变化必须附带可从空库执行的迁移。
- SQLite 开启外键、WAL 和 busy timeout；事务保持短小，不执行网络调用。
- 时间按 UTC 入库，通过可注入 `Clock` 获取；展示和调度使用配置时区。
- 唯一约束、状态枚举和索引承担最终一致性，不用 JSON 替代所有结构化字段。
- 清理任务分批执行，禁止无界全表操作。
- 迁移 PostgreSQL 的触发条件是多实例、高写竞争、高可用或复杂报表，而不是预先设计。

## 5. API 与身份

- API 路由只做认证、校验、Application Service 调用和响应映射，不直接执行发送或 SQL。
- 管理员、API Client、企业微信成员和插件是不同身份，权限不可混用。
- 管理员密码使用强哈希；Access/Refresh Token 可过期和撤销；登录失败受限流保护。
- API Client Key 只展示一次，数据库仅保存哈希、前缀、权限和限流配置。
- 列表接口服务端分页；错误响应结构稳定；内部异常堆栈不返回客户端。
- OpenAPI、Pydantic Schema、API 测试是接口事实源，避免维护重复的手写字段清单。

## 6. 企业微信

- Core 不出现 `touser`、`agentid`、`media_id` 等渠道字段。
- 默认向指定 UserID 发送；空接收人不得隐式变成 `@all`。
- 广播只能由明确授权的后台管理员发起，并写入审计。
- Access Token 缓存必须并发安全；Token 失效只强制刷新重试一次。
- 永久参数或接收人错误不反复重试；所有请求设置连接和总超时。
- 回调先验签解密，再持久化并异步处理；重复回调必须幂等。
- 日志不得记录 Secret、Access Token、完整回调 XML 或敏感正文。

交互式提醒使用普通文本、图文或图片消息，以及应用底部快捷菜单。菜单操作当前用户最近一次成功投递的交互式提醒，不自动回退到更早任务。

## 7. Reminder

提醒定义、执行实例和接收人状态分离：

- `Reminder` 保存长期定义和下一次计划时间；
- `ReminderOccurrence` 表示某次实际执行；
- `ReminderOccurrenceRecipient` 保存每位接收人的确认、通知次数、claim 和下次催办时间。

Planner 只创建持久化实例；Escalation Worker claim 接收人并通过 Event/Delivery 流程发送。只有 Event 返回 `accepted` 或 `duplicate` 后才能推进通知次数。

关键规则：

- Once、Interval、Cron 和兼容 RRULE 都由持久化定义恢复；内存调度器不是事实来源。
- `any` 任一确认完成；`all/each` 全部接收人终止后完成，已确认成员不再催办。
- 完成、停止、取消和过期必须限定 Occurrence，必要时再限定接收人，不能误伤周期提醒的其他实例。
- 持续催办必须有最短间隔、最大次数或截止时间等有界停止条件。
- `@all` 交互式广播创建成员快照，首次原生广播，后续只催办未完成成员。
- “全员完成后通知”仅适用于 `all` 策略，且只有全部快照成员真实完成时触发；停止、取消和过期不算完成。
- Reminder 图片和图文都必须引用有效 `MediaAsset`。

## 8. Plugin Runtime

插件只发现事件，不直接发送通知。插件通过 `PluginContext` 使用配置、Secret、HTTP、状态、媒体、AI、提醒和 `emit_event()`。

每个插件必须有稳定 ID、Manifest、Pydantic 配置、README、固定 fixture 和测试。API v1 插件为受信任进程内插件，默认并发数为 1。

可靠游标顺序：

1. 拉取并规范化来源记录；
2. 按稳定时间或来源 ID 排序；
3. 不匹配记录在成功扫描后 checkpoint；
4. 匹配记录先 emit；
5. 仅在 `accepted` 或 `duplicate` 后 checkpoint；
6. emit 失败时不能越过当前记录。

插件不得访问 ORM、渠道、进程环境或其他插件状态，不得使用随机事件键、`eval`、`exec`、`shell=True` 或动态安装依赖。网络目标、Secret、媒体、AI、广播和提醒权限必须在 Manifest 中按最小范围声明。

项目插件 Skill 位于 `.codex/skills/notify-hub-plugin-development/`。

## 9. AI Gateway

Provider、API Key、模型目录、Profile、缓存、预算和调用日志属于核心平台。插件只能调用 Manifest 授权且能力匹配的 Profile，不能指定 Provider URL、API Key、任意 Header 或绕过安全策略。

- 外部内容视为不可信数据，模型无工具、通知或配置写权限。
- 结构化输出必须通过 Pydantic 校验；不得从任意自然语言猜测业务结论。
- Provider URL 受 HTTPS、SSRF、DNS、实际连接地址和重定向限制。
- API Key 使用 SecretStore；日志和 Invocation 不保存正文、Prompt、Authorization 或原始响应。
- AI 是建议层；事件幂等、是否发送和 checkpoint 由确定性代码负责。
- AI 不可用时核心平台和纯规则插件继续工作；需要 AI 决策的监控默认 fail closed。

## 10. 媒体

- 下载外部媒体时校验协议、DNS、实际连接地址、重定向、MIME、尺寸和时长。
- 文件存储路径由平台生成，禁止调用方选择本地路径，防止路径穿越。
- 企业微信临时素材过期后重新上传，不把 provider media ID 当永久资源。
- 静态资源 URL 和签名媒体 URL 由核心 `PublicMediaUrlBuilder` 统一生成。
- Codex 使用内置静态封面；Romano 下载、处理并保存来源图片；提醒由手机或后台上传为 `MediaAsset`。
- 图文消息使用可公网访问的签名 `picurl`；没有独立跳转 URL 时可使用封面 URL。
- 清理任务必须保护 Reminder、Occurrence 和待投递 Notification 仍引用的媒体。

## 11. Secret、安全与日志

- 真实 `.env`、Token、Cookie、API Key 和数据库文件不得提交。
- 生产主密钥、JWT 和媒体签名密钥必须使用强值；管理 API 只返回是否已配置，不回显明文。
- 外部输入限制大小和长度；HTML 默认按纯文本处理；前端展示时转义。
- 不使用裸 `except:`，不吞异常；错误归一化为稳定、脱敏类型。
- 异步函数中不执行阻塞 I/O；必要时使用线程池。
- 结构化日志包含 request、event、delivery、plugin 等关联 ID，不包含凭据和完整敏感响应。
- 登录、广播、Secret 更新、插件配置、dead 重试、提醒强制操作和维护任务进入审计。

## 12. 测试门禁

测试覆盖应随风险扩大。新增功能至少考虑：

- 正常路径、参数错误、权限错误和重复请求；
- 网络超时、限流、永久错误和降级；
- 服务重启、租约回收、调度恢复和 checkpoint；
- Secret 泄露、SSRF、媒体校验和数据库约束；
- Reminder、Delivery、Plugin 等非法状态转换。

基础门禁：

```powershell
.\.venv\Scripts\ruff.exe format --check backend plugins
.\.venv\Scripts\ruff.exe check backend plugins
.\.venv\Scripts\mypy.exe backend/app plugins
.\.venv\Scripts\pytest.exe

Set-Location frontend
npm run lint
npm run typecheck
npm run test
npm run build
```

发布还需验证 Alembic 空库升级、Docker 构建、Compose 配置、健康检查和关键手工链路。

## 13. 文档维护

- `README.md`：产品介绍、安装和快速使用。
- `PROJECT_GUIDE.md`：当前长期有效的开发约束。
- `docs/DECISIONS.md`：重要架构决策、原因和迁移影响。
- `docs/operations.md`：部署、备份、恢复和故障处理。
- `.codex/PROJECT_ACHIEVEMENTS.md`：从 Git 历史整理的每日完成事项与工程收获，不作为实现规范。
- `docs/archive/design-phase/`：历史设计和开发计划，仅供追溯。

普通实现调整不要求更新历史设计。只有架构、领域状态机、安全边界、平台能力或验收流程改变时才更新总纲；改变已接受决策时新增 ADR，不静默改写历史。
