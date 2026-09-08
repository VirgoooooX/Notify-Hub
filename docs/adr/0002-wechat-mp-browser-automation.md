# 个人公众号基于 Playwright 独立容器全自动发布 (browser 模式)

**状态：superseded（由 `docs/DECISIONS.md` 中 ADR-031 取代）**

> 本文仅保留为历史记录。这里描述的 Notify Hub 内置领取接口、`allow_mp_browser`
> 权限和旧容器均已删除，不得作为现行部署指导。

## 背景与决策

个人主体微信公众号无法使用微信官方群发发布 API（官方接口仅开放给认证账号）。此前由油猴脚本辅助人工发布的方案受宿主浏览器焦点和操作习惯影响较大，难以实现稳定的无人值守自动化。

Notify Hub 采用轻量独立 Playwright 容器架构实现个人订阅号的全自动发布闭环（`mp_publish_mode=browser`）：
1. 插件触发 `publish_to_mp`，通过现有 `mp_article` 渠道将文章存入 `MpArticle` 库（初始状态 `ready`）；
2. 独立 Playwright 容器通过持有 `allow_mp_browser` 权限的标准 `nfy_` API 密钥向 Notify Hub 轮询领取文章（FIFO）；
3. 容器自动操作微信公众号管理平台网页端：注入文章标题、作者、摘要、正文富文本，并自动选取正文首图作为封面；
4. 保存草稿后记录 `draft_saved` checkpoint；
5. 点击发表后记录 `publish_clicked` checkpoint；
6. 轮询已群发列表校验发布结果并回填链接，将文章标记为 `published`。

## 关键约束与设计

- **复用持久队列**：不新建独立的 Browser Job 表，不引入 Redis 或 MQ，复用 `MpArticle` 作为单号串行 FIFO 队列，状态流转为 `ready -> publishing -> published | failed`；
- **核心数据隔离**：Browser 容器绝不直连 SQLite 数据库，仅通过 `/api/v1/admin/mp-browser/*` REST API 与核心交互；
- **防重复群发绝对防线**：文章进入 `publish_clicked` 阶段后，任何超时恢复或重试严禁再次触发“发表”按钮，只能以 `reconcile` 模式只读检查已发布列表；已被点击发表失败的文章禁止通过后台直接 restore 为 `ready`，必须由人工核实；
- **登录态与告警闭环**：Browser 容器每 30s 发送心跳并在检测到扫码登录需求时上传 PNG 格式的二维码；服务端维护会话状态并在超时 45s 时标记 `offline`；登录过期事件通过既有 Event/Notification/Delivery 投递到管理员企业微信，且以稳定 `incident_id` 自动去重；
- **内容保真**：完全保留 Twitter 原文完整展示，文章标题取自 Markdown 顶部 `# 标题`，正文渲染自动去除重复的顶部大标题，防止在公众号中出现双标题。
