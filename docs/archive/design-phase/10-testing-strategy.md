# 测试策略

## 1. 测试目标

Notify Hub 的核心风险不是“页面按钮能不能点”，而是：

- 事件是否会丢失；
- 同一事件是否重复发送；
- 服务重启后任务是否恢复；
- 插件异常是否拖垮核心；
- 企业微信临时故障是否正确重试；
- Secret 是否泄露；
- 自然语言或语音是否误创建提醒；
- 持续催办是否能按确认及时停止。

测试应围绕这些失败模式设计。

## 2. 测试分层

### 2.1 单元测试

目标：验证纯业务逻辑，不访问真实网络和数据库或仅使用内存数据库。

覆盖：

- event key 规范化；
- 幂等判断；
- 重试退避；
- 企业微信错误分类；
- 接收人权限；
- Reminder 状态转换；
- RRULE/next_run_at；
- 持续催办停止条件；
- 插件匹配器；
- 配置 Schema；
- 日志脱敏；
- URL/SSRF 校验；
- 消息长度分块。

### 2.2 仓储与迁移测试

使用临时 SQLite 文件而不是只用内存数据库，验证：

- 外键；
- 唯一约束；
- WAL 配置；
- Alembic 从空库升级；
- 多次升级幂等；
- Event 幂等并发；
- Delivery claim；
- processing 过期回收；
- PluginState 乐观锁；
- 数据保留清理。

### 2.3 API 集成测试

通过 ASGI 测试客户端覆盖：

- 管理员登录；
- API Key 鉴权；
- Client 权限；
- `POST /events` 202；
- 重复请求；
- 422 业务校验；
- 429 限流；
- Secret 不回显；
- 插件管理接口；
- Reminder API；
- health/live 与 health/ready。

### 2.4 Worker 集成测试

使用 FakeChannel：

- pending -> succeeded；
- pending -> retry_wait；
- pending -> dead；
- Worker 在发送中崩溃；
- claim 租约回收；
- 多个待投递按时间处理；
- 过期 Notification 取消；
- 单个接收人失败不影响其他接收人；
- 重启后继续。

### 2.5 插件契约测试

所有内置插件使用同一套 contract tests：

- Manifest 可解析；
- plugin ID 与目录一致；
- config_schema 可生成；
- 无 Secret 时返回可理解错误；
- run 可被超时取消；
- emit_event 使用稳定 event key；
- 不直接导入渠道模块；
- 不访问数据库层；
- 日志包含 plugin_id/run_id；
- 状态读写只通过 Context。

### 2.6 企业微信 Adapter 测试

不直接调用真实企业微信完成 CI，使用 Mock HTTP Server：

- Token 成功；
- Token 缓存；
- 并发只刷新一次；
- Token 失效后强制刷新；
- 文本请求结构；
- 多用户 touser；
- 图文请求结构；
- 超时；
- 5xx；
- 永久业务错误；
- 临时业务错误；
- 响应日志脱敏；
- 代理 base URL。

真实企业微信只用于手工验收或受保护的部署环境测试。

### 2.7 前端测试

- 配置表单 Schema 渲染；
- Secret 字段不回显；
- 创建 API Client 的一次性 Key 提示；
- 插件启停；
- 立即运行防重复点击；
- Notification/Delivery 时间线；
- dead 手工重试；
- 危险操作二次确认；
- 错误状态展示；
- 基础响应式布局。

### 2.8 端到端测试

本地 Compose 启动：

1. 初始化管理员；
2. 创建接收人；
3. 创建 API Client；
4. 调用 Event API；
5. Fake WeCom 接收请求；
6. 后台显示 succeeded；
7. 重复调用显示 duplicate；
8. 重启容器后历史仍存在。

Codex 插件 E2E 使用本地 RSS fixture server，不依赖真实 X。

## 3. 关键场景矩阵

| 场景 | 期望 |
|---|---|
| API 事件写库后进程崩溃 | 重启后继续投递 |
| 同一 event_key 提交 10 次 | 一条 Event、一组 Delivery |
| 企业微信发送超时 | retry_wait，不丢失 |
| 企业微信 UserID 错误 | dead，不无限重试 |
| 插件拉取成功、emit 失败 | 不推进游标 |
| emit 成功但响应丢失 | 下次 duplicate，不重复效果 |
| 插件运行超时 | timed_out，API 正常 |
| 插件连续失败 | degraded/failed 并熔断 |
| Worker 领取后崩溃 | 租约过期后回收 |
| 回调重复三次 | 一条 IncomingMessage 业务处理 |
| 用户发送“确认”但会话过期 | 不创建提醒 |
| 持续催办已确认 | 不再生成后续 Delivery |
| 广播请求无权限 | 403 |
| Secret 查询 | 仅返回 configured |
| 日志异常 | 不包含 Token/Cookie/Secret |

## 4. 时间测试

提醒系统必须使用可注入 Clock，禁止业务逻辑直接散落调用当前时间。

覆盖：

- UTC 与本地时区转换；
- 跨日；
- 月末；
- 闰年；
- 夏令时不存在时间；
- 夏令时重复时间；
- 服务停机期间错过提醒；
- 周期任务 catch-up 策略；
- 催办 stop_at；
- 会话过期。

对“停机期间错过提醒”应明确策略：

- 单次提醒：启动后尽快补发，超过可配置宽限期则标记 missed；
- 周期提醒：默认只补最近一次，不批量补发所有历史周期；
- 持续催办：重新计算下一次，不瞬间补发多条。

## 5. 并发测试

即使 SQLite 单实例，也应测试：

- 两个并发请求提交相同 Event；
- 两个 Worker 尝试 claim 同一 Delivery；
- 两个管理员同时更新插件配置；
- 插件立即运行与计划运行同时触发；
- Token 并发刷新；
- Reminder 确认与 Worker 触发同时发生。

数据库唯一约束和事务是最终防线，不能只依靠进程内锁。

## 6. 故障注入

建议提供测试开关或 Fake 实现注入：

- 网络超时；
- HTTP 500；
- 企业微信业务错误；
- 数据库短暂锁；
- Worker 发送后崩溃；
- 插件运行中取消；
- RSS 返回畸形 XML；
- 磁盘写满；
- 主加密密钥缺失；
- 媒体转码失败。

故障注入代码不得在生产环境通过普通请求开启。

## 7. 安全测试

至少覆盖：

- 无认证访问管理员 API；
- API Key 枚举和时序比较；
- 已吊销 Key；
- Client 越权接收人；
- Client 越权广播；
- 登录暴力尝试；
- CSRF；
- XSS 内容展示；
- SSRF 到 loopback/metadata；
- URL 重定向到内网；
- 路径穿越；
- 恶意文件名；
- 伪造 MIME；
- 超大请求体；
- XML 解析安全；
- 回调签名错误；
- 回调重放；
- 日志注入；
- Secret 序列化泄露。

## 8. 性能基线

个人使用场景无需复杂压测，但应建立最低基线：

- Event API 在正常 SQLite 负载下快速完成持久化；
- 1000 条 pending Delivery 可稳定处理；
- 50 个插件调度记录不会明显拖慢后台；
- 通知历史分页不全表扫描；
- 日志和保留清理不会长期锁库；
- 前端列表使用服务端分页。

性能测试不应调用真实企业微信。

## 9. CI 门禁

每个 PR 至少运行：

- 代码格式检查；
- Lint；
- 静态类型检查；
- 后端单元和集成测试；
- Alembic migration test；
- 前端 lint/typecheck/test/build；
- 插件 contract tests；
- Secret scanning；
- 依赖漏洞扫描；
- Docker build。

主分支发布前增加：

- Compose E2E；
- 数据库升级测试；
- 镜像基本安全扫描；
- 手工企业微信发送验收。

## 10. 测试数据规则

- 不提交真实企业微信 UserID 以外的敏感个人信息；
- 不提交真实 Secret/Token/Cookie；
- X/RSS 使用固定匿名 fixture；
- 时间固定，不依赖测试执行当天；
- 测试数据库和媒体目录每次隔离；
- 随机 ID 可注入或断言格式，不对具体随机值硬编码。

## 11. 发布验收

`v0.1.0` 发布前必须通过：

- [ ] 全量自动化测试；
- [ ] 空数据库安装；
- [ ] 从上一开发数据库升级；
- [ ] Event API 端到端；
- [ ] 指定企业微信成员真实发送；
- [ ] 企业微信临时错误重试演练；
- [ ] 容器重启恢复；
- [ ] Codex 插件 baseline；
- [ ] Codex 插件新匹配 fixture；
- [ ] 重复事件验证；
- [ ] 备份恢复演练；
- [ ] 日志与 Secret 人工检查。
