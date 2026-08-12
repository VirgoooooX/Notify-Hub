# Docker Log Monitor

这是 Notify Hub 的可信私有插件，用 Fleetge Agent 的只读 Docker API 检查多台主机。

## 当前范围

- 检查容器停止、unhealthy、OOMKilled 和重启次数增加；
- 拉取最近增量日志，但只保留明确影响运行的严重模式；
- 识别 Notify Hub 的结构化 `plugin run failed` 日志，按 `plugin_id + error_type` 稳定聚合，不受动态运行 ID 和时间戳影响；
- 首次运行默认只建立 baseline，不补发历史日志；
- 使用持久化状态进行错误指纹、阈值和 30 分钟冷却去重；
- Fleetge Agent 必须连续 3 轮无法读取才通知；任一成功轮次会立即清零，且同一次故障只通知一次；
- 单个容器详情或日志读取失败只跳过该容器，不判定整台 Agent 离线；
- 日志摘要会移除 URL 路径、IP、长 Token/ID 和常见凭据字段；
- 不执行重启、Compose、SSH 或任何修复动作。

## Agent Secret 格式

每个 Manifest Secret 保存一个 JSON 字符串，格式为：

```json
{"base_url":"https://agent.example.invalid/private-path","token":"replace-me"}
```

`base_url` 必须包含 Fleetge Agent 的私密路径；不要把它写入普通插件配置。

## 已知降噪规则

Browserless usage 统计、Redis 启动诊断、探针产生的非法 HTTP 请求、Vaultwarden 图标阻断、qBittorrent 解析噪声、Readflow Postgres 客户端断开、普通 traceback、worker timeout、普通外部 API/DNS/HTTP 错误和 CouchDB 单请求进程 crash 都不会直接告警。Notify Hub 自身的结构化插件运行失败是显式例外；此外仅容器生命周期异常、OOM、segfault、磁盘满/只读、数据库损坏和持续 Supervisor 不可用会通知。

插件为五台主机现场已观察到的每个容器维护显式阈值 profile；日志规则只保留明确影响运行的严重模式，新出现的容器默认不因普通 error/warning 通知，并继续受到生命周期状态检查。
