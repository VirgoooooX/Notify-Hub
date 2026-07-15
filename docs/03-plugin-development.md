# 插件开发规范

## 1. 插件定位

插件负责“发现事件”，不负责“发送通知”。

一个合格插件应只处理来源特有逻辑，例如：

- 查询某个 API、RSS、网页或设备；
- 保存来源游标；
- 判断数据是否满足触发条件；
- 生成稳定事件键；
- 向核心提交标准化事件；
- 报告运行健康状态。

插件禁止：

- 直接调用企业微信；
- 读取平台企业微信 Secret；
- 直接访问业务数据库表；
- 自己实现通知重试和接收人拼接；
- 在日志中输出 Token、Cookie、API Key；
- 使用随机值作为事件幂等键；
- 在主事件循环中执行阻塞 I/O。

## 2. 插件目录

```text
plugins/builtin/codex_x_monitor/
├── manifest.json
├── plugin.py
├── schemas.py
├── matcher.py
├── README.md
└── tests/
    ├── fixtures/
    └── test_plugin.py
```

私有插件放在：

```text
plugins/private/<plugin_id>/
```

第一阶段不支持通过 Web 上传压缩包并动态安装。

## 3. Manifest

示例：

```json
{
  "id": "codex_x_monitor",
  "name": "Codex X Monitor",
  "version": "0.1.0",
  "description": "监控指定 X 账号中的 Codex 用量重置消息",
  "entrypoint": "plugin:CodexXMonitorPlugin",
  "api_version": "1",
  "kind": "monitor",
  "trusted": true,
  "default_schedule": {
    "type": "interval",
    "seconds": 600
  },
  "max_concurrency": 1,
  "timeout_seconds": 60,
  "permissions": {
    "network": ["x.com", "nitter.example.com"],
    "secrets": ["x_api_bearer_token"],
    "broadcast": false,
    "media_write": false,
    "ai_profiles": ["semantic_classifier_fast"]
  }
}
```

规则：

- `id` 使用小写 snake_case，发布后不可改变；
- `version` 使用语义化版本；
- `api_version` 表示 Notify Hub 插件契约版本；
- `entrypoint` 必须指向唯一插件类；
- `permissions` 是声明而不是自动授权，管理员仍需批准；
- 第一阶段 `trusted` 必须为 `true`，只运行已审核代码。

## 4. 基础接口

建议接口：

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PluginMetadata:
    id: str
    name: str
    version: str


@dataclass(frozen=True)
class PluginRunResult:
    status: str
    emitted_events: int = 0
    message: str | None = None


class NotifyPlugin(ABC):
    @classmethod
    @abstractmethod
    def metadata(cls) -> PluginMetadata:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def config_schema(cls) -> Mapping[str, Any]:
        raise NotImplementedError

    async def start(self, context: "PluginContext") -> None:
        pass

    @abstractmethod
    async def run(self, context: "PluginContext") -> PluginRunResult:
        raise NotImplementedError

    async def stop(self) -> None:
        pass

    async def health_check(self, context: "PluginContext") -> dict[str, Any]:
        return {"healthy": True}
```

`run()` 必须可重复调用，不应假设进程永不重启。

## 5. PluginContext

插件只能通过 Context 使用平台能力：

```python
class PluginContext:
    plugin_id: str
    run_id: str
    logger: PluginLogger
    http: RestrictedHttpClient
    ai: PluginAIClient

    async def emit_event(self, event: EventDraft) -> EventReceipt: ...
    async def get_state(self, key: str, default: Any = None) -> Any: ...
    async def set_state(self, key: str, value: Any, expected_version: int | None = None) -> int: ...
    async def get_secret(self, name: str) -> str: ...
    async def get_config(self) -> dict[str, Any]: ...
    async def save_checkpoint(self, values: dict[str, Any]) -> None: ...
```

禁止向插件暴露：

- ORM Session；
- 数据库连接；
- 企业微信 Client；
- 主加密密钥；
- 管理员 JWT；
- 其他插件状态。

`context.ai` 每次调用都检查 Manifest 中的 `ai_profiles`，配置页面选中 Profile 不能替代运行时授权。插件提供具体业务 instruction、内容、标签/字段和缓存键；平台 Profile 决定能力类型、Provider、模型、Key、输出策略、预算、超时与缓存。调用方法必须与 Profile 能力一致，例如 `context.ai.classify()` 只能使用 classify Profile。通用防提示注入、禁用工具和结构化校验由 Gateway 强制执行，插件无需重复声明，也不能关闭。AI 只返回建议，插件仍通过确定性代码决定是否 emit 和何时 checkpoint。

Manifest 的 `permissions.ai_profiles` 同时是插件对 Profile 的依赖声明。平台会从已校验配置中解析实际选择；没有显式选择器的插件按依赖全部获授权 Profile 处理。保存配置和启用插件时都会验证依赖仍可用，防止软删除后重新产生悬空引用。

## 6. EventDraft

插件提交统一结构：

```python
class EventDraft(BaseModel):
    event_type: str
    event_key: str
    title: str
    content: str
    level: Literal["info", "warning", "critical"] = "info"
    occurred_at: datetime | None = None
    url: AnyHttpUrl | None = None
    image_url: AnyHttpUrl | None = None
    recipients: list[str] | None = None
    require_ack: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)
```

规则：

- `event_type` 使用点分命名，例如 `codex.usage_reset`；
- `event_key` 在插件来源内稳定唯一；
- `title` 和 `content` 不得包含 Secret；
- `payload` 用于审计和模板，不得直接映射为渠道请求；
- `recipients` 为空时使用插件配置或平台默认路由；
- 插件请求 `critical`、广播或语音投递时，核心必须再次进行权限检查。

## 7. 状态与游标

插件状态属于采集侧，例如：

```json
{
  "last_seen_post_id": "2061106703446450392",
  "last_success_at": "2026-07-13T12:00:00Z",
  "consecutive_source_failures": 0
}
```

推荐更新顺序：

1. 获取来源数据；
2. 生成 EventDraft；
3. 调用 `emit_event()`；
4. 确认核心返回 accepted 或 duplicate；
5. 更新游标。

当 `emit_event()` 因平台错误失败时，不推进游标。

重复提交并不可怕，核心依靠幂等键返回 duplicate。丢失事件不可接受。

## 8. 配置 Schema

插件使用 JSON Schema 或 Pydantic 模型声明配置。配置 UI 根据 Schema 自动生成。

示例：

```python
class CodexXConfig(BaseModel):
    enabled: bool = True
    username: str = "thsottiaux"
    interval_seconds: int = Field(default=600, ge=60, le=86400)
    data_source: Literal["x_api", "rss"] = "rss"
    keywords: list[str] = ["codex", "reset", "usage"]
    recipients: list[str] = []
    first_run_mode: Literal["baseline", "scan_recent"] = "baseline"
```

敏感字段不得作为普通字符串配置：

错误：

```json
{"x_api_token": "secret"}
```

正确：

```json
{"x_api_token_secret_ref": "plugin/codex_x_monitor/x_api_bearer_token"}
```

## 9. 调度

支持两种首期调度：

### interval

```json
{"type": "interval", "seconds": 600}
```

### cron

```json
{"type": "cron", "expression": "0 */6 * * *", "timezone": "Asia/Shanghai"}
```

要求：

- 最短间隔由平台统一限制；
- 插件运行默认不重叠；
- 上一次仍在运行时，下一次标记为 skipped；
- 管理员“立即运行”也遵守并发限制；
- 调度配置写数据库，不仅存在内存中；
- 进程重启后恢复下一次运行。

## 10. 超时、重试和熔断

来源请求重试由插件或受限 HTTP Client 在一次运行内部完成；通知投递重试由核心完成，两者不可混淆。

推荐：

- 插件整体运行超时：默认 60 秒；
- 单次 HTTP 请求超时：连接 5 秒，总计 20 秒；
- 来源临时错误：最多 2 次指数退避；
- 连续插件运行失败 5 次：状态改为 degraded；
- 连续失败 10 次：暂停自动调度，发送平台级告警；
- 手动运行成功后可恢复调度。

不得无限重试。

## 11. 日志

插件日志必须自动附带：

- plugin_id；
- plugin_run_id；
- trace_id；
- source operation。

示例：

```python
context.logger.info(
    "fetched posts",
    extra={"post_count": len(posts), "latest_id": latest_id},
)
```

禁止输出：

- Authorization；
- Cookie；
- 企业微信 Secret；
- API Key；
- 完整个人隐私内容；
- 大体积响应正文。

## 12. 网络访问

第一阶段 Trusted Plugin 仍需遵循网络白名单：

- 使用平台提供的 HTTP Client；
- 默认阻止访问环回地址、链路本地地址和云元数据地址；
- 管理员可为私有监控显式允许局域网地址；
- 跟随重定向后再次校验目标；
- 响应体有大小上限；
- 禁止插件自行关闭 TLS 验证。

这是为了避免 SSRF 和误访问内网敏感服务。

## 13. 原生插件与脚本适配器

### 原生插件

适合：

- 需要状态；
- 需要复杂解析；
- 长期维护；
- 需要配置 UI；
- 需要可靠测试。

### 受控脚本适配器

后续可提供通用插件，运行管理员预先挂载的脚本。规则：

- 使用 argv 数组，禁止 `shell=True`；
- 只能运行允许目录中的文件；
- 环境变量白名单；
- 超时后终止进程树；
- stdout 限长；
- stdout 必须是标准事件 JSON；
- stderr 进入插件日志；
- 第一阶段不开放网页上传脚本。

示例输出：

```json
{
  "triggered": true,
  "event_type": "nas.disk_temperature_high",
  "event_key": "nas-z4s-disk-2-high",
  "title": "NAS 硬盘温度过高",
  "content": "Disk 2 当前温度 58°C",
  "level": "warning"
}
```

## 14. 版本兼容

插件 Manifest 包含 `api_version`。核心升级规则：

- 同一 major API version 保持向后兼容；
- 废弃字段至少保留一个发布周期；
- 不兼容插件不加载，并在后台显示原因；
- PluginState 迁移由插件提供显式 migration；
- 插件版本升级前保存配置和状态备份。

## 15. 测试要求

每个内置插件至少具备：

- 配置校验测试；
- 首次 baseline 测试；
- 新事件测试；
- 重复事件测试；
- 来源超时测试；
- 核心 emit 失败时不推进游标测试；
- 日志脱敏测试；
- 超时和取消测试；
- 固定 fixture，不依赖真实外部网络完成单元测试。

## 16. 插件评审清单

合并插件前必须确认：

- [ ] 有稳定 plugin ID 和 event key；
- [ ] 不直接调用渠道；
- [ ] 不直接访问数据库；
- [ ] Secret 使用引用；
- [ ] 所有网络请求有超时；
- [ ] 响应体有限制；
- [ ] 首次运行不会误发历史内容；
- [ ] emit 失败不会丢游标；
- [ ] 重复执行不会重复产生效果；
- [ ] 有测试和 README；
- [ ] 没有 `shell=True`、`eval`、`exec` 或动态安装依赖。
