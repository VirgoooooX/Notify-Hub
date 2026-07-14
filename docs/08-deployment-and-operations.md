# 部署与运维

## 1. 第一阶段部署形态

第一阶段使用一个 Docker 镜像和一个持久化数据目录：

```text
reverse proxy
    │ HTTPS
    ▼
notify-hub container
    ├── FastAPI API
    ├── static frontend
    ├── delivery worker
    ├── reminder worker
    ├── plugin scheduler/worker
    └── SQLite
```

这是单实例架构。不要同时启动两个使用同一 SQLite 数据库的 Notify Hub 副本。

## 2. 推荐目录

```text
/opt/notify-hub/
├── compose.yml
├── .env
├── data/
│   ├── notify-hub.db
│   ├── plugins/
│   ├── media/
│   └── backups/
└── logs/
```

目录权限只授予运行容器所需 UID/GID。敏感凭证直接在 `.env` 中声明，不用创建 `secrets/` 物理目录。

## 3. Compose 示例

```yaml
services:
  notify-hub:
    image: notify-hub:0.3.0
    container_name: notify-hub
    restart: unless-stopped
    environment:
      # 1. 基础运行与代理
      FORWARDED_ALLOW_IPS: "*"
      NOTIFY_HUB_ENVIRONMENT: "production"
      NOTIFY_HUB_PUBLIC_BASE_URL: "${NOTIFY_HUB_PUBLIC_BASE_URL:?set the public HTTPS URL}"

      # 2. 企业微信出站
      NOTIFY_HUB_WECOM_CORP_ID: "${NOTIFY_HUB_WECOM_CORP_ID:?set the WeCom Corp ID}"
      NOTIFY_HUB_WECOM_AGENT_ID: "${NOTIFY_HUB_WECOM_AGENT_ID:?set the WeCom Agent ID}"
      NOTIFY_HUB_WECOM_API_BASE_URL: "${NOTIFY_HUB_WECOM_API_BASE_URL:-https://qyapi.weixin.qq.com}"
      NOTIFY_HUB_ALLOW_BROADCAST: "true"

      # 3. 敏感凭据（直接以环境变量形式注入）
      NOTIFY_HUB_SECRET_ENCRYPTION_KEY: "${NOTIFY_HUB_SECRET_ENCRYPTION_KEY:?set the encryption key}"
      NOTIFY_HUB_JWT_SECRET: "${NOTIFY_HUB_JWT_SECRET:?set the JWT secret}"
      NOTIFY_HUB_WECOM_SECRET: "${NOTIFY_HUB_WECOM_SECRET:-}"
      NOTIFY_HUB_WECOM_CALLBACK_TOKEN: "${NOTIFY_HUB_WECOM_CALLBACK_TOKEN:-}"
      NOTIFY_HUB_WECOM_CALLBACK_AES_KEY: "${NOTIFY_HUB_WECOM_CALLBACK_AES_KEY:-}"
    ports:
      - "127.0.0.1:8788:8000"
    volumes:
      # 将宿主机目录挂载到应用相对路径默认值，实现免配置直接持久化
      - ./data:/app/data
      - ./logs:/app/logs
      - ./media:/app/data/media
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 45s
    security_opt:
      - no-new-privileges:true
```

正式 Compose 应固定镜像版本，不长期使用不可追踪的 `latest`。仓库中的 [`deploy/docker-compose.yml`](../deploy/docker-compose.yml) 还显式列出了核心的运行参数。Compose 通过同级目录的 `.env` 进行环境变量插值传递，不再使用 Docker Secrets 文件挂载，降低单机部署复杂度。

## 4. 反向代理

要求：

- HTTPS；
- 保留真实客户端 IP；
- 限制请求体大小；
- 为企业微信回调配置稳定公网 URL；
- 不缓存 API；
- 静态资源可缓存；
- 仅开放必要端点；
- 后端端口绑定到本机或 Docker 内网，不直接暴露公网。

示例边界：

```text
公开：
/health/live
/api/v1/events
/api/v1/channels/wecom/callback
/admin 或前端入口

仅管理员认证后可用：
/api/v1/admin/*
```

不建议通过 IP 白名单替代应用层鉴权，但可作为附加保护。

## 5. 首次启动

1. 创建持久化目录；
2. 生成强随机主加密密钥；
3. 配置企业微信 CorpID、AgentID 和 Secret；
4. 启动容器；
5. 完成管理员初始化；
6. 进入后台测试企业微信 Token；
7. 创建 Person 和 WeComIdentity；
8. 发送指定用户测试消息；
9. 配置企业微信回调地址；
10. 验证文字回调；
11. 创建 Codex 插件配置；
12. 立即运行一次 baseline；
13. 启用调度；
14. 检查备份任务。

## 6. 配置分层

### 环境变量

适合：

- 运行模式；
- 端口；
- 数据库 URL；
- 公网基础 URL；
- 默认时区；
- 日志级别；
- 敏感 Secret 密钥。

### 数据库配置

适合：

- 插件启停和普通配置；
- 调度规则；
- 接收人；
- API Client 权限；
- 数据保留；
- 通知默认策略。

### Secret 存储

适合：

- 企业微信 Secret；
- 回调 Token/AES Key；
- 插件 Token/Cookie；
- API Key 哈希；
- 应用主密钥。

环境变量覆盖数据库配置时，应在后台明确显示“由环境变量管理，无法在线修改”。

企业微信 API 可通过 `NOTIFY_HUB_WECOM_API_BASE_URL` 指向受信任的 HTTPS 反向代理，支持路径前缀。该代理必须原样转发 `cgi-bin/*`，并会接触渠道 Secret、Access Token 与消息内容；不要使用不受信任的公共代理。修改地址后必须重启应用，使 Token 缓存和 HTTP 连接池一并重建。

## 7. 数据库运维

SQLite 建议：

- WAL；
- foreign_keys=ON；
- busy_timeout；
- 定期 `PRAGMA integrity_check`；
- 定期 checkpoint；
- 清理任务分批；
- 备份使用 SQLite 在线备份方式或短暂停止写入后的文件快照；
- 不直接在运行中复制可能未 checkpoint 的数据库文件作为唯一备份。

数据库迁移：

1. 启动前自动检查；
2. 默认自动执行向前迁移；
3. 迁移前创建备份；
4. 失败则不启动 Worker；
5. 不自动执行破坏性降级；
6. 发布说明列出不可逆迁移。

## 8. 备份策略

建议：

- 每日数据库备份；
- 保留最近 7 份日备份；
- 保留最近 4 份周备份；
- 主加密密钥单独离线备份；
- 重要插件私有文件一并备份；
- 临时媒体通常不备份；
- 备份文件加密；
- 定期恢复演练。

备份成功通知不能只依赖被备份的 Notify Hub 自己。至少通过宿主日志或外部监控确认备份任务状态。

## 9. 升级流程

1. 阅读发布说明；
2. 记录当前镜像版本；
3. 停止插件调度或进入维护模式；
4. 等待当前短任务完成；
5. 创建数据库和配置备份；
6. 拉取固定版本镜像；
7. 启动并执行迁移；
8. 检查 ready；
9. 发送企业微信测试消息；
10. 手动运行 Codex 插件；
11. 检查待投递队列；
12. 恢复调度；
13. 保留旧镜像用于回滚。

如果数据库迁移不可逆，回滚必须同时恢复旧数据库备份。

## 10. 监控指标

第一阶段即使不接 Prometheus，也应在后台或内部指标端点提供：

- events accepted total；
- events duplicate total；
- deliveries pending/retry/dead；
- delivery success rate；
- delivery latency；
- provider error count；
- plugin run success/failure；
- plugin consecutive failures；
- worker heartbeat；
- database size；
- media storage size；
- reminder overdue count。

后续可暴露 `/metrics`。

## 11. 日志轮转

建议容器输出结构化日志到 stdout，由 Docker 或宿主收集。若同时写文件：

- 按大小或日期轮转；
- 限制保留天数；
- 压缩历史文件；
- 不把调试级敏感信息长期保留；
- 插件日志通过字段区分，不为每个插件无限创建文件。

## 12. 故障排查顺序

### 收不到通知

1. Event 是否已创建；
2. Notification/Delivery 是否已创建；
3. Delivery 当前状态；
4. DeliveryAttempt 错误类型；
5. 接收人 UserID 是否正确；
6. 是否在应用可见范围；
7. 企业微信 Token 测试；
8. 代理是否可用；
9. 是否被限流或去重；
10. Worker 心跳。

### 插件不运行

1. 插件是否 enabled；
2. 配置是否有效；
3. next_run_at；
4. 是否有运行中的旧任务；
5. 是否被熔断；
6. Secret 是否配置；
7. 最近 PluginRun；
8. 来源网络是否允许；
9. 调度器心跳。

### 重复通知

1. event_key 是否稳定；
2. source_id 是否变化；
3. 插件是否在 emit 成功前推进或重置游标；
4. Event 唯一约束是否存在；
5. Worker 是否重复 claim；
6. 企业微信响应丢失后是否重发；
7. 多个 Notify Hub 实例是否共享同一 SQLite。

## 13. 容量预估

个人使用场景建议默认上限：

- 插件数：50；
- 活跃插件并发：5；
- 单插件默认并发：1；
- 外部事件：每分钟 60/Client；
- 日通知量：数千以内；
- 单消息正文：数十 KB 以内，最终按渠道限制转换；
- SQLite 数据库：数 GB 内保持简单运维；
- 历史通过保留策略定期清理。

达到这些边界前无需 Redis 或消息中间件。

## 14. 迁移到多进程/多实例

触发条件：

- 事件量明显增加；
- 插件执行阻塞核心；
- 需要高可用；
- SQLite 写锁成为瓶颈；
- 需要第三方不可信插件。

迁移路径：

1. SQLite -> PostgreSQL；
2. Scheduler 只保留一个 leader；
3. API 与 Worker 分进程；
4. 使用数据库安全 claim；
5. 插件 Worker 子进程化；
6. 必要时引入 Redis 做缓存/锁，而不是先入为主；
7. 保持 API、Event 和 PluginContext 契约不变。

## 15. 运维验收清单

- [ ] 使用固定镜像版本；
- [ ] 容器非 root；
- [ ] 后端端口不直接公网暴露；
- [ ] HTTPS 正常；
- [ ] 企业微信回调可验证；
- [ ] 数据目录持久化；
- [ ] 主加密密钥已离线备份；
- [ ] 数据库每日备份；
- [ ] 完成恢复演练；
- [ ] Worker 心跳可见；
- [ ] dead 队列可查询；
- [ ] 日志轮转；
- [ ] 发送测试成功；
- [ ] 插件 baseline 成功；
- [ ] 升级和回滚流程有记录。
