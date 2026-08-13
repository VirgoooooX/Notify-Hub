# Notify Hub 公众号导入助手（Tampermonkey）

在公众号后台「新建图文」页面把 Notify Hub 文章库中 `ready`（待发布）的文章一键填入编辑器：标题、作者、摘要、正文。**发布按钮不会自动点击**，最终发布由你人工确认。

## 原理

- 公众号官方发布 API 需要企业主体/认证账号；个人订阅号没有对应权限。
- 本脚本走「人工最终发布」路径：Notify Hub 只负责生成、排版和保存文章，脚本复用你已登录的公众号后台会话填表，最后一步由人点击发布。
- 官方 API 路径（`NOTIFY_HUB_MP_PUBLISH_MODE=draft|publish`）保留，与文章库模式并存。

## 安装

1. 浏览器安装 Tampermonkey（Chrome/Edge）。
2. 打开本目录的 [notify-hub-mp-import.user.js](./notify-hub-mp-import.user.js)，Tampermonkey 会提示安装；或复制内容到 Tampermonkey「新建脚本」。
3. 打开 `https://mp.weixin.qq.com` 任意页面，点击 Tampermonkey 图标 → Notify Hub 公众号导入助手 → 设置菜单：

   - **设置 Notify-Hub 地址**：你的后台地址，例如 `http://localhost:8000`（必须能被浏览器访问；建议走 HTTPS 或内网域名，避免混合内容限制）。
   - **设置访问令牌**：粘贴 Notify-Hub 管理员 Access Token。自托管环境可登录后台后从浏览器 LocalStorage 的 `notify_hub_access_token` 复制；后续 Token 过期重新复制即可。

## 使用

1. 进入公众号后台 → 「图文消息」→「新建图文」。
2. 页面右上角出现「Notify-Hub 导入」面板，自动列出 `ready` 文章。
3. 点「填入编辑器」：标题/作者/摘要/正文自动填入；正文为公众号友好的富文本 HTML。
4. 封面：面板会给出封面 URL，请在公众号后台点「从正文选择」使用首图（正文首图即封面），或手动上传。
5. 检查内容后，**人工点击微信后台的发布按钮**。
6. 发布完成后回到 Notify Hub「公众号文章」工作台，把该文章标记为「已发布」。

> 若自动填表未命中字段（微信改版），面板里「复制正文」会把富文本复制到剪贴板，在正文区 `Ctrl+V` 即可；标题/摘要手填。

## 安全说明

- 令牌只保存在 Tampermonkey 脚本自身的存储（`GM_setValue`）中，不会写进页面 LocalStorage；但它是管理员令牌，请只在你信任的浏览器/设备上使用。
- 脚本只读取文章列表和详情，不做任何写操作；发布始终由人触发。
- 微信公众号编辑器 DOM 可能变化；字段选择器不命中时优先使用「复制正文」兜底。

## 与官方 API 路径的关系

| 路径 | 条件 | 行为 |
| --- | --- | --- |
| 文章库模式（library） | 未配置 AppID/Secret，或 `NOTIFY_HUB_MP_PUBLISH_MODE=library` | 文章进入工作台，脚本/复制导入公众号，人工发布 |
| 草稿模式（draft） | 配置了 AppID/Secret 且 `NOTIFY_HUB_MP_PUBLISH_MODE=draft` | 官方 API 建草稿，不自动发布 |
| 自动发布（publish） | 配置了 AppID/Secret 且 `NOTIFY_HUB_MP_PUBLISH_MODE=publish` | 官方 API 建草稿并提交发布 |