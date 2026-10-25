# Codex X Monitor

内置插件 `codex_x_monitor` 监控指定 X 账号的时间线，发现与 Codex 用量重置、额度恢复或限制变化有关的帖子后向 Notify Hub 核心提交 `codex.usage_reset` 事件。时间线由平台统一读取；生产可以用 twscrape 主抓，失败时由平台单次降级到 RSSHub。

## 边界与可靠性

- 插件只通过 `PluginContext` 获取配置、状态、Secret、受限 HTTP 和提交事件；不导入 ORM 或企业微信渠道。
- 事件键固定为 `x-post-<post_id>`。两个 Provider 对同一帖子生成相同键，自动降级时仍由核心幂等。
- 默认首次运行执行 baseline，只保存最新稳定帖子 ID，不通知历史内容。
- Feed 会先按发布时间和数字帖子 ID 升序处理。状态 v3 以已见帖子 ID 集合处理晚到内容，高水位只增不减，并在 AI 判定或提交事件前保存未完成帖子原文；即使帖子离开最新抓取窗口，重启后仍会合并恢复。已处理帖子逐条移出待处理集合；无关帖子也推进游标，匹配帖子仅在核心返回 `accepted` 或 `duplicate` 后推进。v1/v2 状态自动迁移，v1 迁移只标记旧时间点以前已抓到的内容，不补发历史消息。
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

`source`、`feed_url` 和 `twscrape_fetch_limit` 仅为旧版本配置迁移保留，平台启动时会把 Provider 选择归一化为平台 X 数据源；新配置不应再写入这些字段。RSSHub 返回的条目必须能从 `guid`、Atom `id` 或帖子链接解析数字 X 帖子 ID。插件不会以标题或正文哈希代替稳定 ID。

## twscrape 主源与降级

当部署设置 `NOTIFY_HUB_X_SOURCE_PROVIDER=twscrape` 时，平台先使用 twscrape；Cookie 无效、版本不兼容或请求失败会只尝试一次 RSSHub。降级和两边都失败都会通过现有健康告警通知；连续直接成功后再发送恢复通知。不要在插件配置中保存 Cookie，也不要另外运行一套并行 Provider。

## 匹配

匹配文本先做 Unicode NFKC、转小写、去 URL/mention 和空白归一化。直接重置需要上下文与重置/额度语义；明确的 `banked reset` 或 `reset card` 加上执行/落地承诺时，即使没有 Codex 关键词也算重置。任何否定规则优先排除，询问、讨论或提议是否重置会被高置信度排除。

每次规则判定同时产生 `matched` 与 `confidence`。明确否定/询问和明确的额度重置公告具有高置信度；只有上下文或弱关键词的暧昧文本具有较低置信度。在 `rules_then_ai` 模式下，仅当规则置信度低于 `rule_ai_threshold`（默认 `0.8`）时调用 AI；`ai_min_confidence` 则控制 AI 返回 `notify` 后真正发送所需的最低置信度。管理员可以提供正则表达式覆盖默认列表。

AI 判定 Prompt 除相邻帖子外，还会从已抓取时间线中挑选最多 4 条较早的相关重置公告，避免无关帖子挤掉关键语境。它会区分直接重置、`banked reset`、明确的 `reset card`、承诺/落地和提问/否定语义；历史只能帮助解释目标帖的指代，不能补出目标帖未确认的执行状态、承诺、时间、用户范围或效果。帖子正文始终按不可信数据处理。

目标帖有相关信号但 AI 返回 `uncertain` 或低于置信度阈值时，插件会向配置的 `recipients` 提交 `codex.usage_reset_review` 警告事件，不生成公众号或小红书发布任务。若没有显式接收人，插件会记录警告并让本次运行失败、保留游标，补上接收人后可手动补扫。AI 调用失败或待核查事件未被核心受理时同样不推进该帖游标。未完成帖子从此版本开始持久保存；升级前已经离开抓取窗口的旧失败帖子不会凭空恢复，需要单独补入源记录。

连续失败触发熔断后，平台插件 Worker 会提交 `system.plugin_circuit_open` 告警；手动运行成功并恢复调度后提交 `system.plugin_recovered`。告警使用插件显式接收人；默认 Profile 的 X 插件未配置接收人时，可使用平台 `NOTIFY_HUB_X_HEALTH_ALERT_RECIPIENT_IDS`。没有接收人不广播；核心受理失败保留事件身份重试，同一故障周期不会重复创建通知。

## 公众号发布（可选）

- 开启 `publish_to_official_account` 后，命中事件会同时生成 `mp_article` 投递，由平台发布到微信公众号；
- `wechat_mp_publish_mode` 可选 `publish`（默认，自动发表）或 `draft`（只保存草稿）；发布逻辑完全位于核心渠道，插件不接触公众号凭证；
- 可选 `article_ai_profile` 只用于把帖子翻译成中文并写成摘要正文；未配置或 AI 失败时回退到确定性摘要；文章提示词要求事实性、正常口语化，不使用标题党表达；

## 小红书发布（可选）

- 平台设置中的“小红书平台发布总开关”默认关闭；关闭时核心会丢弃小红书投递意图，不会创建或投递小红书文章；
- 只有平台总开关开启后，插件配置中的 `publish_to_xiaohongshu` 才会生成小红书图文任务；
- `xiaohongshu_publish_mode` 可选 `draft`（默认，只保存草稿）或 `publish`（自动发布）。
- `xiaohongshu_visibility` 控制公开/私密；`xhs_cover_image_url` 可选，未设置时由插件生成动态封面。
- 是否发布仍由规则判定与 `ai_min_confidence` 置信度阈值决定，AI 摘要不参与发布决策。

## 测试

从仓库根目录运行：

```powershell
uv run --locked --extra dev pytest plugins/builtin/codex_x_monitor/tests -q
```
