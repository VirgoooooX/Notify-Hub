# 企业微信接入设计

## 1. 应用规划

为 Notify Hub 新建独立企业微信自建应用，建议名称：

```text
系统通知
```

不要复用 `family-finance` 或 MoviePilot 的应用凭据。三套系统保持独立：

- family-finance：家庭财务业务；
- MoviePilot：影视自动化业务；
- Notify Hub：通用监控、提醒和系统通知。

Notify Hub 配置：

- CorpID；
- AgentID；
-应用 Secret；
- 回调 Token；
- EncodingAESKey；
- 可选消息代理地址；
- 默认接收人；
- 管理员 UserID 白名单。

所有凭据只通过环境变量、挂载 Secret 文件或加密 Secret 存储提供。

## 2. 可见范围和接收人

企业微信应用只能向其可见范围内的成员发送消息。

平台内部使用 `Person` 和 `WeComIdentity` 管理身份，最终由渠道适配层转换为企业微信 `UserID`。

示例：

```text
person_vigoss -> WeCom UserID: Vigoss
```

多用户发送时，适配层负责转换为企业微信要求的 `touser` 字符串。广播使用 `@all`，但必须同时满足：

1. API Client 或插件具备广播权限；
2. 请求显式指定广播；
3. 系统未启用广播禁用开关；
4. 记录审计日志。

## 3. 出站消息能力

### 3.1 文本消息

适用于：

- 普通提醒；
- 监控告警；
- 含链接的通知；
- 持续催办。

渠道适配层输入：

```python
ChannelMessage(
    message_type="text",
    title="NAS 硬盘温度过高",
    content="Disk 2 当前温度 58°C",
    url="https://nas.example.com",
    recipients=["Vigoss"],
)
```

适配层负责：

- 标题与正文格式；
- 超长内容按 UTF-8 字节安全拆分；
- 多段消息顺序发送；
- 链接附加；
- 企业微信错误码归一化。

### 3.2 图文消息

输入：

- 标题；
- 摘要；
- 图片 URL；
- 跳转 URL。

适用于：

- 推文或文章通知；
- 报表摘要；
- 带封面的媒体内容；
- 可点击详情页的告警。

如果图片 URL 不可被企业微信访问，应先下载并转存到 Notify Hub 可公开访问的受控媒体地址，或降级为文本消息。

### 3.3 图片消息

流程：

1. 下载或读取图片；
2. 校验 MIME、大小和像素限制；
3. 上传企业微信临时素材；
4. 获取 `media_id`；
5. 发送图片消息；
6. 缓存短期有效的素材 ID；
7. 过期后重新上传。

第一阶段可先实现图文消息，独立图片消息放在第二阶段。

### 3.4 语音消息

流程：

1. 获取已有语音文件，或由 TTS 生成；
2. 转码为企业微信支持的音频格式；
3. 校验时长和大小；
4. 上传临时素材；
5. 发送 voice 消息；
6. 失败时按策略降级为文本。

建议仅允许 warning/critical 级别或显式请求发送语音，避免普通通知滥用。

## 4. Access Token 管理

实现要求：

- 按 CorpID + AgentID/Secret 维度缓存；
- 保存绝对过期时间；
- 在过期前提前刷新；
- 并发刷新使用锁，避免惊群；
- 发送接口返回 Token 失效错误时强制刷新并最多重试一次；
- Token 不写数据库、不写日志；
- 代理地址变化后清空缓存；
- 获取 Token 和发送消息均设置明确超时。

伪代码：

```python
async def get_access_token(force: bool = False) -> str:
    if not force and cache.is_valid(skew_seconds=120):
        return cache.value

    async with refresh_lock:
        if not force and cache.is_valid(skew_seconds=120):
            return cache.value
        token = await request_new_token()
        cache.replace(token)
        return token
```

## 5. 网络错误分类

可重试：

- 连接超时；
- 读取超时；
- DNS 临时故障；
- HTTP 5xx；
- 企业微信明确的临时服务错误；
- Token 失效（强制刷新后重试一次）；
- 代理临时不可用。

不可重试：

- CorpID、AgentID 或 Secret 错误；
- 接收人不在应用可见范围；
- UserID 不存在；
- 消息格式非法；
- 素材格式不支持；
- API 权限不足；
- 请求被平台业务规则永久拒绝。

企业微信原始错误码应映射为平台错误类别：

```text
AUTH_INVALID
RECIPIENT_INVALID
RECIPIENT_NOT_VISIBLE
PAYLOAD_INVALID
MEDIA_INVALID
RATE_LIMITED
PROVIDER_TEMPORARY
NETWORK_ERROR
UNKNOWN_PROVIDER_ERROR
```

## 6. 出站重试

投递 Worker 推荐退避：

```text
第 1 次失败：30 秒
第 2 次失败：2 分钟
第 3 次失败：10 分钟
第 4 次失败：1 小时
第 5 次失败：进入 dead
```

critical 通知可配置不同策略，但不得无限重试。

如果消息包含多个分块：

- 每块单独记录 provider response；
- 一旦中间块失败，Delivery 保持失败并重试；
- 为避免成功块重复，可在消息层保存分块游标；
- MVP 可接受整条重发，但必须通过企业微信 duplicate check 或平台短期去重降低重复风险。

## 7. 入站回调

企业微信回调包含两类请求：

### 7.1 URL 验证

```http
GET /api/v1/channels/wecom/callback
```

处理步骤：

1. 读取签名参数；
2. 验证签名；
3. 解密 `echostr`；
4. 返回纯文本；
5. 不创建业务事件。

### 7.2 消息和事件

```http
POST /api/v1/channels/wecom/callback
```

处理步骤：

1. 校验请求大小；
2. 验证签名；
3. 解密 XML；
4. 解析消息类型；
5. 生成 `IncomingMessage`；
6. 持久化并快速返回；
7. 后台交给 ConversationService；
8. 使用消息 ID 或内容指纹防止回调重试重复处理。

回调接口不使用普通 API Key，但必须通过企业微信签名和加密校验。

## 8. 入站消息类型

### 文本

直接进入命令或自然语言提醒解析。

### 语音

优先使用回调中的识别文本。识别文本为空时：

1. 保存媒体引用；
2. 通过企业微信媒体下载接口取回语音；
3. 使用 ASR 转写；
4. 转写成功后进入与文本相同的流程；
5. 失败时回复明确提示，不创建提醒。

### 图片

第一阶段只保存媒体引用并回复“暂不支持通过图片创建提醒”。第二阶段可用于 OCR 或附件提醒。

### 菜单事件

推荐菜单：

- 今日提醒；
- 全部提醒；
- 待确认；
- 帮助。

菜单命令也必须执行管理员/用户权限检查。

## 9. 对话安全

- 只接受应用可见范围内的合法成员；
- 可配置管理员 UserID 白名单；
- 普通成员只能管理自己的提醒；
- 广播、管理插件、查看系统状态仅管理员可用；
- 自然语言解析结果必须二次确认；
- 高风险操作使用明确命令或按钮确认；
- 会话有过期时间；
- 回调日志不保存完整语音、文本隐私或解密后的原始 XML；
- 语音媒体按保留策略自动删除。

## 10. 自然语言提醒流程

```text
用户：明天下午三点提醒我交电费

系统：
准备创建提醒：
- 内容：交电费
- 时间：2026-07-14 15:00
- 接收人：你
- 重复：不重复
回复“确认”创建，回复“取消”放弃。
```

确认后才调用 ReminderService。

解析结果至少包含：

- intent；
- title/content；
- local datetime；
- timezone；
- recurrence；
- recipients；
- require_ack；
- repeat interval；
- confidence；
- ambiguities。

存在歧义时必须询问，不得猜测。

## 11. 代理支持

若企业微信要求固定出口 IP，可配置：

```text
NOTIFY_HUB_WECOM_API_BASE_URL=https://your-proxy.example.com/wecom
```

要求：

- 通过环境变量配置，修改后重启生效；
- 必须使用 HTTPS；
- 代理地址不得包含用户名、密码、查询参数或 fragment；
- 支持路径前缀，代理必须原样转发其下的 `cgi-bin/*` 路径、查询参数和请求体；
- 代理会接触 CorpID、应用 Secret、Access Token 和消息内容，只能使用完全受信任的服务；
- 不接受请求级任意代理 URL；
- 健康测试分别检查 Token 和发送接口；
- 代理不可用时事件仍可入库并等待重试。

## 12. 配置项建议

```text
WECOM_CORP_ID
WECOM_AGENT_ID
WECOM_SECRET
WECOM_CALLBACK_TOKEN
WECOM_CALLBACK_AES_KEY
WECOM_API_BASE_URL
WECOM_DEFAULT_RECIPIENT_IDS
WECOM_ADMIN_USER_IDS
WECOM_TOKEN_REFRESH_SKEW_SECONDS
WECOM_REQUEST_TIMEOUT_SECONDS
```

实际生产环境应优先通过 Secret 文件或容器 Secret 注入，而非直接写入 compose 文件。

## 13. 验收清单

- [ ] URL 验证成功；
- [ ] 指定单个 UserID 发送成功；
- [ ] 多 UserID 发送成功；
- [ ] 未授权广播被拒绝；
- [ ] Token 缓存和并发刷新正确；
- [ ] Token 失效可刷新重试；
- [ ] 不可见 UserID 显示明确错误；
- [ ] 网络超时进入 retry_wait；
- [ ] 服务重启后继续投递；
- [ ] 回调重复不会重复创建提醒；
- [ ] 文本消息可以创建提醒草稿；
- [ ] 语音识别失败不会误建提醒；
- [ ] 日志不包含 Secret、Token 和解密 XML。
