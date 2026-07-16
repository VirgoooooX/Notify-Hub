# AGENTS.md

本文件面向在本仓库中工作的 AI 编码助手和开发者。开始修改代码前，必须先阅读：

1. `README.md`
2. `PROJECT_GUIDE.md`
3. 涉及架构决策时读取 `docs/DECISIONS.md`
4. 涉及部署、恢复或提醒运维时读取 `docs/operations.md`

## 1. 当前开发方向

当前项目已进入完整平台迭代阶段。新增能力必须建立在可靠 Event/Notification/Delivery、持久化调度、权限和恢复机制之上，不得绕过核心链路直接实现渠道或临时任务。

第一条目标闭环：

```text
Codex X Monitor
  -> emit Event
  -> Event/Notification/Delivery 落库
  -> Delivery Worker
  -> 企业微信指定 UserID
  -> 后台查看投递结果
```

## 2. 不可违反的架构约束

1. 插件不得直接调用企业微信渠道；
2. 插件不得直接访问 ORM Session 或业务表；
3. API 路由不得直接执行发送逻辑；
4. Event 在返回 202 前必须持久化；
5. 网络请求不得放在数据库事务内；
6. 所有外部事件必须有稳定 `event_key`；
7. 敏感 Secret 优先在本地环境或环境变量中声明以防明文入仓，允许不通过 Docker Secret 文件物理隔离以降低部署复杂度；
8. 企业微信 `@all` 必须显式授权；
9. Reminder 触发后通过 Event/Delivery 流程发送；
10. 内存调度器不是事实来源，重启必须可恢复；
11. 第一阶段不得实现网页上传任意插件或脚本；
12. 不得为了“以后可能需要”提前引入 Redis、RabbitMQ 或微服务。

## 3. 实现优先级

新增功能按以下顺序落地：

1. 明确领域状态、权限、幂等和恢复语义；
2. 完成数据库模型、迁移和 Application Service；
3. 通过 Event/Notification/Delivery 或持久化 Worker 接入外部效果；
4. 增加 API、后台或移动端入口；
5. 补齐失败、重启、权限、安全和数据库约束测试；
6. 最后处理展示优化和非关键增强。

不得为了赶页面或单次联调绕过可靠核心，也不得开放网页上传任意插件或脚本。

## 4. 代码边界

推荐依赖方向：

```text
api -> application -> domain
                    -> infrastructure interfaces
channels -> domain/application interfaces
plugin_runtime -> application interfaces
plugins -> PluginContext only
workers -> application services
```

禁止反向依赖：

```text
plugin -> channels.wecom
plugin -> infrastructure.database
api -> channels.wecom.client
web -> database
```

## 5. Python 代码要求

- 使用类型标注；
- Pydantic 模型用于 API/配置边界；
- 领域状态转换使用显式方法，不散落字符串赋值；
- 时间通过可注入 Clock；
- 所有外部 HTTP 请求有连接和总超时；
- 异步函数中不执行阻塞 I/O；
- 同步阻塞逻辑使用线程池或后续 Worker；
- 不使用裸 `except:`；
- 不吞异常；
- 错误要归一化为稳定类型；
- 不使用 `eval`、`exec`、`shell=True`；
- 不在模块导入时启动后台任务；
- 应用启动和关闭通过生命周期管理。

## 6. 数据库要求

- 模型变更必须带 Alembic migration；
- 迁移必须可从空库执行；
- 唯一约束是幂等最终防线；
- 事务保持短小；
- 网络调用在事务提交后执行；
- SQLite 开启外键、WAL 和 busy timeout；
- 不把 JSON 当成所有数据的替代品；
- 状态枚举和索引遵循当前 ORM、Alembic、领域方法和测试；
- 删除使用明确保留策略，不做无界全表操作。

## 7. 插件要求

每个插件必须：

- 有 `manifest.json`；
- 有稳定 ID；
- 有配置模型；
- 有 README；
- 有固定 fixture；
- 有单元测试；
- 使用 `PluginContext`；
- 在 emit accepted/duplicate 后才推进游标；
- 有超时；
- 不泄露 Secret；
- 默认首次 baseline 不误发历史数据。

## 8. API 要求

- 外部事件 API 返回 202；
- 管理员 API 和 Client API 分离认证；
- 错误响应结构统一；
- OpenAPI 与实现同步；
- 列表接口服务端分页；
- 危险操作需要审计；
- Secret GET 只返回配置状态；
- 不把内部异常堆栈返回给客户端。

## 9. 企业微信要求

- Core 不出现 `touser`、`agentid`、`media_id` 等渠道字段；
- Token 缓存并发安全；
- Token 失效只强制刷新重试一次；
- 永久错误不重复重试；
- 指定 UserID 是默认模式；
- 空接收人不得隐式变成 `@all`；
- 日志不得记录 Secret、Access Token、完整回调 XML；
- 回调先验签解密，再持久化并异步处理。

## 10. 测试要求

新增功能至少包含：

- 正常路径；
- 参数错误；
- 权限错误；
- 重复请求；
- 网络超时；
- 服务重启/恢复相关测试；
- Secret 泄露检查；
- 数据库约束测试。

涉及 Event/Delivery/Plugin/Reminder 状态时，必须测试非法状态转换。

## 11. PR 要求

PR 描述应包含：

- 解决的问题；
- 对应 Phase；
- 主要设计；
- 数据库迁移；
- 安全影响；
- 测试结果；
- 手工验收；
- 回滚方式；
- 未完成项。

不要在一个 PR 同时实现多个 Phase 的大量功能。

## 12. 修改文档

普通实现调整不要求修改历史设计资料。改变项目总纲、运维流程或已接受架构决策时，应在同一 PR 更新 `PROJECT_GUIDE.md`、`docs/operations.md` 或新增 ADR，不得静默改写历史决策。

## 13. 当前迭代原则

- 小步完成单一可验证垂直切片；
- 优先修复可靠性、安全和恢复问题，再扩展新能力；
- 共享能力进入核心或明确的公共模块，业务规则留在所属领域或插件；
- 不把历史开发路线图当作当前任务清单，实际范围以用户请求、`PROJECT_GUIDE.md` 和现有代码为准。
