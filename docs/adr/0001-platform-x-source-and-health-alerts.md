# 平台统一 X 数据源与健康告警

**状态：accepted**

Notify Hub 将 X 时间线定义为平台级能力：生产默认通过 RSSHub 读取，RSSHub 自己管理 X Cookie/auth_token；twscrape 仅作为显式开启的冷备 Provider，不自动切换。两个内置 X 插件只通过 `PluginContext.x` 获取规范化内容，数据源不可用、账号级失败、恢复和可选内容静默均由持久化健康 Worker 记录状态，并通过 Event/Notification/Delivery 投递企业微信；这样既不把渠道或凭证泄漏到插件，也避免 RSSHub 短暂故障时静默丢失监控结果。

## Consequences

- 插件配置不再承载 RSSHub 地址、RSSHub Token、X Cookie 或 Provider 选择；这些属于平台部署配置。
- RSSHub 失灵时系统默认告警但不偷偷切换 twscrape，避免两套游标、重复通知和故障原因被掩盖。
- twscrape 代码和锁定依赖保留，只有将 `NOTIFY_HUB_X_SOURCE_PROVIDER` 显式改为 `twscrape` 才会使用；切回 RSSHub 同样需要显式部署变更。
- 健康告警接收人使用 Notify Hub 内部 Person ID；空接收人不会隐式广播到 `@all`。
