# twscrape 主源、RSSHub 单次降级与帖子对账

**状态：accepted**

## 决策

在部署显式设置 `NOTIFY_HUB_X_SOURCE_PROVIDER=twscrape` 时，平台先使用 twscrape 抓取 X 时间线。单次抓取如果出现 Cookie 无效、twscrape 与 X 页面不兼容或其他 twscrape 错误，只请求一次 RSSHub；RSSHub 成功就返回降级结果，两边都失败才返回数据源不可用。

降级、全部不可用和恢复都复用现有 `XHealthService -> EventService -> Notification -> Delivery` 链路。告警使用稳定错误码，不把 Cookie 或第三方异常原文写入日志、事件 payload 或插件配置。RSSHub 仍可作为通用默认值；不做双轮正常轮询、复杂熔断器或新消息队列。

插件状态使用 v2：

- `recent_processed_ids` 判断帖子是否已经处理；
- `last_seen_post_id` 只作为单调不回退的高水位，不能单独决定帖子是否新；
- v1 迁移时，把当前抓到且不晚于旧 `last_seen_published_at` 的帖子标为已见，不补发旧事件；
- 迁移截止时间会阻止之后晚到的旧帖子被无界回放，截止时间之后的帖子仍会进入候选。

规则明确把带有执行/落地承诺的 `banked reset` 和原文明确写出的 `reset card` 视为重置信号。文章提示词要求完整引用原文、事实性标题和正常口语；`banked reset` 不得被自动写成“重置卡”。

## 影响

- 依赖锁定到 `twscrape 0.20.1`，应用发布版本为 `0.9.9`，插件版本为 `0.6.1`；
- 不新增数据库表或 Alembic migration；
- RSSHub 结果仍通过稳定 `x-post-<post_id>` 事件键幂等；
- 旧 ADR-001 中“twscrape 只作人工冷备、不自动切换”的部分由本 ADR 取代，平台级能力和健康告警边界保持不变。
