# Fabrizio HWG Monitor

内置插件 `fabrizio_hwg_monitor` 读取 Fabrizio Romano 的 X 时间线，识别 “HERE WE GO” 转会消息并提交 `football.transfer_here_we_go` 事件。图片由核心媒体能力下载、处理和持久化，插件不直接调用企业微信。

## 数据源边界

- X 内容由平台统一的 `PluginContext.x` 能力提供，生产默认读取 RSSHub；插件配置页面不提供 RSSHub 地址、Token、Cookie 或 Provider 选择。
- RSSHub 的 X Cookie/auth_token 只在 RSSHub 自己的部署中维护。Notify Hub 只读取规范化时间线。
- 仓库仍保留 twscrape 作为冷备 Provider，但不会自动故障切换；需要在平台部署配置中显式选择 `twscrape`，并完成独立验证后才会使用。
- 默认首次运行是 baseline，不会把历史转会消息一次性发送。
- 事件键固定为 `fabrizio-hwg-<post_id>`；只有核心返回 `accepted` 或 `duplicate` 后才推进游标。

## 平台配置

平台部署环境变量由 `deploy/docker-compose.yml` 传入：

```dotenv
NOTIFY_HUB_X_SOURCE_PROVIDER=rsshub
NOTIFY_HUB_RSSHUB_BASE_URL=http://rsshub:1200
NOTIFY_HUB_RSSHUB_ACCESS_KEY=
NOTIFY_HUB_X_HEALTH_ALERT_ENABLED=true
NOTIFY_HUB_X_HEALTH_ALERT_RECIPIENT_IDS=["person_admin"]
```

健康 Worker 会独立检查启用的 X 插件账号。连续失败达到阈值时产生系统告警；恢复需要连续成功达到恢复阈值。账号不存在等账号级错误不会误报成 RSSHub 整体故障。可通过插件配置中的 `content_silence_alert_enabled` 和 `content_silence_seconds`，或平台默认值启用“请求正常但长期没有新推文”的提醒。

## 测试

```bash
uv run --locked --extra dev pytest plugins/builtin/fabrizio_hwg_monitor/tests -q
```
