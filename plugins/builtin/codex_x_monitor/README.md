# Codex X Monitor

内置插件 `codex_x_monitor` 监控指定 X 账号的 RSS/Atom Feed，或可选的 X API v2，发现与 Codex 用量重置、额度恢复或限制变化有关的帖子后向 Notify Hub 核心提交 `codex.usage_reset` 事件。

## 边界与可靠性

- 插件只通过 `PluginContext` 获取配置、状态、Secret、受限 HTTP 和提交事件；不导入 ORM 或企业微信渠道。
- 事件键固定为 `x-post-<post_id>`。RSS 与 X API 对同一帖子生成相同键，切源时仍由核心幂等。
- 默认首次运行执行 baseline，只保存最新稳定帖子 ID，不通知历史内容。
- Feed 会先按发布时间和数字帖子 ID 升序处理。无关帖子也推进游标；匹配帖子仅在核心返回 `accepted` 或 `duplicate` 后推进。
- HTTP 请求总超时为 20 秒。X API Bearer Token 只从名为 `x_api_bearer_token` 的插件 Secret 获取，绝不进入普通配置或日志。

## RSS 配置示例

```json
{
  "enabled": true,
  "username": "thsottiaux",
  "source": "rss",
  "feed_url": "https://rss.example.com/thsottiaux/rss",
  "interval_seconds": 600,
  "first_run_mode": "baseline",
  "recipients": []
}
```

RSS/Atom 条目必须能从 `guid`、Atom `id` 或帖子链接解析数字 X 帖子 ID。插件不会以标题或正文哈希代替稳定 ID。
实际部署时应将 `manifest.json` 中的 `rss.example.com` 替换为管理员审核过的 Feed 主机；运行时网络权限不会接受未列入 manifest 的任意域名。

## X API

将 `source` 设为 `x_api`，并由平台为插件配置 `x_api_bearer_token` Secret。普通配置中不得保存 Token。429 响应会转换为明确的 `SourceRateLimited`，不会在插件内高频循环重试。

## 匹配

匹配文本先做 Unicode NFKC、转小写、去 URL/mention 和空白归一化。规则要求同时命中 Codex 上下文与重置/额度语义，任何否定规则优先排除。询问、讨论或提议是否重置（例如 `Should we reset ...?`）会被高置信度排除。

每次规则判定同时产生 `matched` 与 `confidence`。明确否定/询问和明确的额度重置公告具有高置信度；只有上下文或弱关键词的暧昧文本具有较低置信度。在 `rules_then_ai` 模式下，仅当规则置信度低于 `rule_ai_threshold`（默认 `0.8`）时调用 AI；`ai_min_confidence` 则控制 AI 返回 `notify` 后真正发送所需的最低置信度。管理员可以提供正则表达式覆盖默认列表。

## 测试

从仓库根目录运行：

```powershell
python -m pytest plugins/builtin/codex_x_monitor/tests -q
```
