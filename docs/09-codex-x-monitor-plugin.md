# Codex X Monitor 插件设计

## 1. 目标

监控指定 X 账号发布的新内容，在识别到 Codex 用量重置、额度恢复或相关官方说明时，向 Notify Hub 核心提交事件，由核心发送企业微信通知。

首个默认监控账号：

```text
thsottiaux
```

插件不直接调用企业微信，也不保存企业微信凭据。

## 2. 插件 ID 与事件

```text
plugin_id: codex_x_monitor
event_type: codex.usage_reset
```

事件键：

```text
x-post-<post_id>
```

示例：

```text
x-post-2061106703446450392
```

同一条帖子无论通过 RSS 还是 X API 获取，必须生成相同 event key。

## 3. 数据源

第一阶段支持两种适配器：

### 3.1 RSS/Atom

优先用于低成本部署。配置一个可访问的 RSS/Atom Feed URL。

优点：

- 无需 X API 凭据；
- 实现简单；
- 适合低频轮询。

风险：

- 第三方 Feed 可能延迟、失效或改变格式；
- 可能缺少稳定帖子 ID；
- 可能截断正文；
- 可能受到访问限制。

适配器必须从 entry ID、guid 或链接中解析稳定帖子 ID。无法解析稳定 ID 的条目不得直接使用标题哈希替代，除非显式启用兼容模式。

### 3.2 X API

可选增强。使用插件 Secret：

```text
x_api_bearer_token
```

优点：

- 数据结构稳定；
- 帖子 ID 明确；
- 支持 since_id；
- 时间和作者字段更可靠。

风险：

- 配额、费用和权限变化；
- Token 管理；
- API 限流。

插件需要把 API 限流映射为可理解的健康状态，不应高频重试。

## 4. 配置模型

```python
class CodexXMonitorConfig(BaseModel):
    enabled: bool = True
    username: str = "thsottiaux"
    source: Literal["rss", "x_api"] = "rss"
    feed_url: AnyHttpUrl | None = None
    interval_seconds: int = Field(default=600, ge=60, le=86400)
    first_run_mode: Literal["baseline", "scan_recent"] = "baseline"
    scan_recent_limit: int = Field(default=10, ge=1, le=100)
    recipients: list[str] = []
    notification_level: Literal["info", "warning"] = "info"
    include_reposts: bool = False
    include_replies: bool = False
    match_mode: Literal["rules", "rules_then_llm"] = "rules"
    positive_patterns: list[str] = DEFAULT_POSITIVE_PATTERNS
    required_context_patterns: list[str] = DEFAULT_CONTEXT_PATTERNS
    negative_patterns: list[str] = DEFAULT_NEGATIVE_PATTERNS
```

MVP 只实现 `rules`。`rules_then_llm` 留到后续，且 LLM 只能辅助分类，不能绕过事件幂等和规则校验。

## 5. 默认匹配策略

匹配必须同时满足：

1. 出现 Codex/OpenAI Codex 上下文；
2. 出现额度重置、恢复、刷新或限制变化语义；
3. 不命中明显否定或无关语义。

示意规则：

### 上下文词

```text
codex
openai codex
codex usage
codex limits
```

### 正向语义

```text
reset
resets
resetting
refreshed
restored
back to normal
usage limit
rate limit
quota
allowance
weekly limit
```

### 否定/排除

```text
not reset
won't reset
cannot reset
unrelated benchmark
quoted old post
```

匹配前处理：

- Unicode 规范化；
- 转小写；
- 合并连续空白；
- 保留原文用于通知；
- URL 和 mention 可从匹配文本中剥离；
- 不对原文做破坏性修改。

规则配置应允许管理员调整，但提供安全默认值。

## 6. 首次运行

默认 `baseline`：

1. 拉取最新条目；
2. 找到最新稳定帖子 ID；
3. 保存为 `last_seen_post_id`；
4. 不产生事件；
5. PluginRun 显示 `baseline_initialized`。

这避免首次启用时把历史帖子全部通知。

`scan_recent` 仅供管理员明确选择：

- 最多扫描配置数量；
- 仍使用 event key 去重；
- 配置页面明确提示可能发送历史通知；
- 修改到该模式需要二次确认。

## 7. 每次运行流程

```text
加载配置和状态
  -> 拉取来源
  -> 解析帖子
  -> 按时间/ID 升序排序
  -> 找出 last_seen 之后的帖子
  -> 逐条执行过滤和匹配
  -> 匹配时 emit_event
  -> accepted/duplicate 后记录已处理
  -> 所有新帖子处理完成后更新 last_seen
  -> 保存 last_success_at
```

关键规则：

- 不仅在匹配时推进游标；不匹配的新帖子也应被标记为已扫描；
- 但如果处理中途核心 emit 失败，不得越过失败帖子整体推进到更高游标；
- 可保存 `processed_post_ids` 短期集合辅助处理乱序 Feed；
- Feed 返回顺序不可信，必须排序；
- 帖子删除不应导致游标回退。

## 8. 建议状态

```json
{
  "schema_version": 1,
  "last_seen_post_id": "2061106703446450392",
  "last_seen_published_at": "2026-07-13T10:00:00Z",
  "recent_processed_ids": [
    "2061106703446450392"
  ],
  "last_success_at": "2026-07-13T10:05:00Z",
  "last_source": "rss"
}
```

`recent_processed_ids` 有上限，例如 200 条，防止状态无限增长。

## 9. 标准帖子模型

不同来源先转换为统一结构：

```python
class XPost(BaseModel):
    id: str
    author_username: str
    author_display_name: str | None = None
    text: str
    url: AnyHttpUrl
    published_at: datetime
    is_repost: bool = False
    is_reply: bool = False
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)
```

匹配器不得直接依赖 RSS XML 或 X API 原始字段。

## 10. 事件内容

```python
await context.emit_event(
    EventDraft(
        event_type="codex.usage_reset",
        event_key=f"x-post-{post.id}",
        title="Codex 用量可能已重置",
        content=format_post_summary(post),
        level=config.notification_level,
        occurred_at=post.published_at,
        url=post.url,
        recipients=config.recipients or None,
        payload={
            "post_id": post.id,
            "author": post.author_username,
            "matched_rules": match_result.matched_rules,
            "source": config.source,
        },
    )
)
```

通知正文建议：

```text
@thsottiaux 发布了与 Codex 用量重置相关的新消息：

<推文摘要>
```

标题使用“可能已重置”比绝对断言更稳妥，除非规则明确识别到官方确认语义。

## 11. 图文通知

MVP 默认文本 + 原文链接。

后续图文模式：

- 标题：Codex 用量可能已重置；
- 摘要：截断后的帖子正文；
- 图片：固定 Codex/Notify Hub 封面或帖子媒体首图；
- URL：原帖。

不要依赖抓取 X 网页截图作为首版功能。

## 12. 错误处理

### 来源超时

- 本次 PluginRun failed；
- 不修改游标；
- 使用受控退避重试最多两次；
- 连续失败后由插件宿主降级或熔断。

### Feed 格式错误

- 保存有限响应元数据，不保存完整敏感内容；
- 不推进游标；
- 显示明确 parser error。

### X API 限流

- 读取可用的限流重置时间；
- 不立即循环重试；
- PluginRun 标记 source_rate_limited；
- 下次调度不早于安全时间。

### emit_event 失败

- 当前帖子不标记完成；
- 运行失败；
- 下次再次提交；
- 核心幂等防止已经接受但响应丢失造成重复。

## 13. 数据源切换

从 RSS 切换 X API 或反向切换时：

- 保留相同 `last_seen_post_id`；
- 新来源必须能产生同样的帖子 ID；
- 无法映射时进入“重新 baseline”流程并要求管理员确认；
- 不自动清空状态；
- 切换动作进入审计日志。

## 14. 插件后台

配置页面显示：

- 启用状态；
- 账号；
- 数据源；
- Feed URL 或 X API Secret 状态；
- 检查间隔；
- 匹配规则；
- 接收人；
- 首次运行模式；
- 上次成功；
- 最近帖子 ID；
- 连续失败；
- 下次运行；
- 立即运行；
- 重新 baseline（危险操作）。

运行详情显示：

- 拉取数量；
- 新帖子数量；
- 匹配数量；
- 产生事件数量；
- 游标前后值；
- 错误类型；
- 运行耗时。

## 15. 测试 Fixture

至少准备：

1. 空 Feed；
2. 只有历史帖子；
3. 新的无关帖子；
4. 新的明确 reset 帖子；
5. 包含否定语义的帖子；
6. 多条新帖子乱序；
7. 重复 guid；
8. 缺少帖子 ID；
9. 截断正文；
10. RSS 解析错误；
11. X API 429；
12. emit_event 抛错；
13. accepted 后状态更新；
14. duplicate 后状态更新；
15. 数据源切换。

## 16. 单元测试关键断言

- baseline 不 emit；
- scan_recent 才扫描历史；
- event key 稳定；
- 同一帖子不同来源 event key 相同；
- 不匹配帖子推进扫描游标；
- emit 失败不越过失败帖子；
- duplicate 被视为核心已接受；
- 多条帖子按升序处理；
- `include_reposts=false` 生效；
- `include_replies=false` 生效；
- 日志不包含 Bearer Token；
- 配置间隔低于平台最小值被拒绝。

## 17. 从旧脚本迁移

旧的独立监控脚本中可复用：

- X API/RSS 拉取逻辑；
- 关键词/正则判断；
- 推文链接生成；
- 首次 baseline；
- last_seen 状态语义；
- 测试样例。

需要删除或替换：

- 直接 Webhook/企业微信通知；
- 独立 `.env` 中的通知 URL 和 Key；
- 自己的调度循环；
- 自己的通知重试；
- 自己的发送状态文件。

迁移后：

- 调度由 Plugin Runtime；
- 状态由 PluginContext；
- 事件由 EventService；
- 通知由 Delivery Worker；
- 接收人由平台配置。

## 18. 完成定义

- [ ] RSS baseline 正常；
- [ ] X API baseline 正常（启用该适配器时）；
- [ ] 新匹配帖子产生事件；
- [ ] 指定成员收到企业微信通知；
- [ ] 同一帖子不重复通知；
- [ ] 服务重启保留游标；
- [ ] 来源失败不丢事件；
- [ ] emit 失败不推进游标；
- [ ] 管理后台可配置和立即运行；
- [ ] 所有 fixture 测试通过；
- [ ] 连续运行一周无重复和漏报的已知问题。
