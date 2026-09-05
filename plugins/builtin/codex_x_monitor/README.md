# Codex X Monitor

内置插件 `codex_x_monitor` 监控指定 X 账号的时间线，发现与 Codex 用量重置、额度恢复或限制变化有关的帖子后向 Notify Hub 核心提交 `codex.usage_reset` 事件。生产时间线由平台统一的 X 数据源能力读取，默认是 RSSHub。

## 边界与可靠性

- 插件只通过 `PluginContext` 获取配置、状态、Secret、受限 HTTP 和提交事件；不导入 ORM 或企业微信渠道。
- 事件键固定为 `x-post-<post_id>`。RSSHub 与冷备 Provider 对同一帖子生成相同键，切源时仍由核心幂等。
- 默认首次运行执行 baseline，只保存最新稳定帖子 ID，不通知历史内容。
- Feed 会先按发布时间和数字帖子 ID 升序处理。无关帖子也推进游标；匹配帖子仅在核心返回 `accepted` 或 `duplicate` 后推进。
- 插件本身不配置 RSSHub 地址、RSSHub Token、X Cookie 或 Provider；这些由平台部署环境管理，绝不进入普通插件配置或日志。
- 平台健康 Worker 独立检查启用账号，在连续失败、恢复或可选的内容静默时通过核心 Event/Delivery 流程告警。

## 插件配置示例

```json
{
  "enabled": true,
  "username": "thsottiaux",
  "source": "rsshub",
  "fetch_limit": 40,
  "interval_seconds": 600,
  "first_run_mode": "baseline",
  "recipients": []
}
```

`source`、`feed_url` 和 `twscrape_fetch_limit` 仅为旧版本配置迁移保留，平台启动时会把它们归一化为 RSSHub；新配置不应再写入这些字段。RSSHub 返回的条目必须能从 `guid`、Atom `id` 或帖子链接解析数字 X 帖子 ID。插件不会以标题或正文哈希代替稳定 ID。

## twscrape 冷备

仓库保留 twscrape 适配器，便于 RSSHub 故障时人工切换验证。它不是插件页面的可选项，也不会自动故障转移；切换需显式设置 `NOTIFY_HUB_X_SOURCE_PROVIDER=twscrape`、提供平台级 `NOTIFY_HUB_X_TWSCRAPE_COOKIE` 并重新部署。切回 RSSHub 也必须显式部署变更。

## 匹配

匹配文本先做 Unicode NFKC、转小写、去 URL/mention 和空白归一化。规则要求同时命中 Codex 上下文与重置/额度语义，任何否定规则优先排除。询问、讨论或提议是否重置（例如 `Should we reset ...?`）会被高置信度排除。

每次规则判定同时产生 `matched` 与 `confidence`。明确否定/询问和明确的额度重置公告具有高置信度；只有上下文或弱关键词的暧昧文本具有较低置信度。在 `rules_then_ai` 模式下，仅当规则置信度低于 `rule_ai_threshold`（默认 `0.8`）时调用 AI；`ai_min_confidence` 则控制 AI 返回 `notify` 后真正发送所需的最低置信度。管理员可以提供正则表达式覆盖默认列表。

AI 判定 Prompt 包含账号背景和对最近约 150 条历史帖的人工抽样总结。它会识别直接确认、近期落地预告、活跃用户里程碑、`reset button` 玩笑、`reseted` / `brand new usage` 等账号特定表达，也会明确排除提问、建议、`but no` 和按钮尚未使用等否定语义。每个待判定帖子还会携带有界的前后时间线供跨帖消歧；帖子正文始终按不可信数据处理，不能覆盖平台安全指令。

## 公众号发布（可选）

- 开启 `publish_to_official_account` 后，命中事件会同时生成 `mp_article` 投递，由平台发布到微信公众号；
- 平台需要配置公众号 AppID/Secret 与发布模式（`publish` 建草稿并提交发布，`draft` 只保存草稿）；发布逻辑完全位于核心渠道，插件不接触公众号凭证；
- 可选 `article_ai_profile` 只用于把帖子翻译成中文并写成摘要正文；未配置或 AI 失败时回退到确定性摘要；
- 是否发布仍由规则判定与 `ai_min_confidence` 置信度阈值决定，AI 摘要不参与发布决策。

## 测试

从仓库根目录运行：

```powershell
uv run --locked --extra dev pytest plugins/builtin/codex_x_monitor/tests -q
```
