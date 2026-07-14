# Notify Hub

Notify Hub 是一个面向个人与家庭场景的企业微信通知、提醒和监控插件平台。

项目采用**模块化单体**架构：核心通知平台与监控插件位于同一仓库、同一部署单元和同一管理后台中，但通过稳定的插件契约、事件模型和权限边界进行隔离。

## 项目定位

Notify Hub 负责：

- 接收外部系统提交的结构化事件；
- 通过企业微信应用向指定成员或全部成员发送通知；
- 管理文本、图文、图片和语音通知；
- 管理单次提醒、周期提醒和需要确认的持续催办；
- 接收企业微信文字、语音、菜单和交互卡片回调；
- 通过自然语言创建、查询、延后、完成或取消提醒；
- 安装、配置、启停和调度监控插件；
- 统一处理去重、重试、发送记录、权限和审计。

其中企业微信入站对话、语音和持续催办属于后续版本；首个 MVP 先建立可靠事件投递和插件平台。

监控插件负责：

- 连接特定数据源；
- 保存采集游标和来源状态；
- 判断是否发生值得通知的事件；
- 向 Notify Hub 核心提交标准化事件。

插件不得直接调用企业微信接口，也不得绕过核心通知服务写入投递记录。

## 首个落地场景

第一阶段内置 `Codex X Monitor` 插件：

1. 定期检查指定 X 账号的新内容；
2. 判断是否出现 Codex 用量重置相关信息；
3. 使用推文 ID 生成稳定的 `event_key`；
4. 向核心提交标准化事件；
5. 由核心完成去重、指定人员投递和发送日志记录。

## 推荐技术形态

- 后端：Python、FastAPI、Pydantic、SQLAlchemy、Alembic；
- 前端：Vue 3、TypeScript、Vite；
- 初始数据库：SQLite；
- 调度：持久化任务调度器；
- HTTP 客户端：支持超时、连接池和受控重试的异步客户端；
- 部署：单仓库、单镜像、单容器，后续可拆分 API 与 Worker；
- 默认时区：可配置，所有数据库时间统一存储为 UTC。

## `v0.1.0` MVP 范围

首个可用版本必须完成：

- 管理员登录；
- 企业微信应用配置与连通性测试；
- 指定企业微信 UserID 的文本通知；
- 图文通知；
- 通知事件 API；
- API Client 与独立 API Key；
- 事件去重；
- 持久化发送队列；
- 发送日志和失败重试；
- 插件注册、配置、启停、立即运行和定时执行；
- 插件健康状态；
- Codex X Monitor 插件；
- 管理后台；
- Docker 部署与数据持久化。

`v0.1.0` 不做：

- 企业微信文字/语音创建提醒；
- 周期提醒和持续催办；
- 独立图片和语音消息；
- 多租户 SaaS；
- 在线安装任意第三方代码；
- 无限制执行用户上传脚本；
- Redis、RabbitMQ 或微服务拆分；
- Telegram、短信、邮件等额外渠道；
- 复杂 AI Agent；
- 未经确认直接由大模型写入提醒数据库。

企业微信入站文字提醒和持续催办计划进入 `v0.2.0`。持续催办采用企业微信交互式模板卡片：每次提醒包含“已完成”按钮，点击回调后原子停止后续提醒。语音输入、ASR、TTS 和语音投递计划进入 `v0.3.0`。

## 文档

详细设计文档位于 [`docs/`](docs/)：

- [产品边界与用例](docs/00-product-scope.md)
- [系统架构](docs/01-architecture.md)
- [领域模型与数据库设计](docs/02-domain-model.md)
- [插件开发规范](docs/03-plugin-development.md)
- [API 契约](docs/04-api-contracts.md)
- [企业微信接入](docs/05-wecom-integration.md)
- [开发路线图](docs/06-development-roadmap.md)
- [安全与可靠性](docs/07-security-and-reliability.md)
- [部署与运维](docs/08-deployment-and-operations.md)
- [Codex X Monitor 插件设计](docs/09-codex-x-monitor-plugin.md)
- [测试策略](docs/10-testing-strategy.md)
- [可交互持续提醒设计](docs/11-interactive-continuous-reminders.md)
- [架构决策记录](docs/DECISIONS.md)
- [编码 Agent 与开发约束](AGENTS.md)

## 核心原则

1. **核心与插件解耦**：插件只负责发现事件，核心负责通知投递。
2. **事件先落库再投递**：避免请求成功但消息丢失。
3. **至少一次接收，效果上只发送一次**：依靠 `event_key` 与幂等约束实现。
4. **默认安全**：所有外部 API 必须鉴权，密钥不得明文入库或写入日志。
5. **可恢复**：服务重启后，待执行任务、提醒、插件状态和待投递消息必须继续工作。
6. **先做可信内置插件**：第三方插件隔离和市场机制放到后续阶段。
7. **企业微信只是渠道，不是业务核心**：提醒、事件和插件模型不依赖企业微信字段命名。
8. **交互回调只改变业务状态一次**：按钮重复点击、旧卡片点击和回调重试必须幂等。

## 当前状态

当前代码已完成 `v0.3.0` 的 Phase 0～9 累计范围，包括可靠事件投递、企业微信渠道、插件运行时与 Codex X Monitor、管理后台、提醒/持续催办、回调交互及受控图片/语音能力。冻结后的发布边界和门禁见 [`docs/12-v0.3.0-release-contract.md`](docs/12-v0.3.0-release-contract.md)。

真实企业微信文本、交互卡片、图片、语音和回调仍需在配置有效凭据的部署环境完成手工验收；ASR/TTS 依赖部署方配置本地 Adapter，不在基础镜像内置模型。

## 本地开发与测试

后端要求 Python 3.12，前端要求 Node.js 22。Windows 上可直接双击仓库根目录的 `start-dev.cmd`，或在 PowerShell 中执行：

```powershell
.\scripts\start-dev.ps1
```

脚本会自动创建 `.venv`、按锁文件变化安装前后端依赖、升级本地 SQLite 数据库，并分别打开后端和前端开发窗口。默认浏览器会打开 `http://127.0.0.1:5173`；关闭两个开发窗口即可停止服务。若不需要自动打开浏览器：

```powershell
.\scripts\start-dev.ps1 -NoBrowser
```

如需从同一局域网内的其他设备访问，使用 `-Lan` 让前后端监听所有本机网卡：

```powershell
.\scripts\start-dev.ps1 -Lan
```

脚本会显示检测到的局域网访问地址。首次使用时，Windows 防火墙可能要求允许专用网络访问 TCP 5173 和 8000；不要在不受信任的公共网络上开放开发服务。`-Lan` 可与 `-NoBrowser` 同时使用。

如需通过反向代理域名访问 Vite 开发服务，显式传入允许的主机名；多个主机名使用逗号分隔。该值只进入当前前端进程，不需要把私人域名提交到 `vite.config.ts`：

```powershell
.\scripts\start-dev.ps1 -Lan -AllowedHosts notify.example.com
```

如需在本机安全重置管理员密码，运行以下命令并按提示隐藏输入新密码；重置会同时撤销现有刷新会话：

```powershell
.\.venv\Scripts\python.exe -m app.cli.reset_admin_password --username admin
```

首次启动后，在管理后台创建管理员账号，密码至少 12 个字符。本地开发数据库位于 `data/notify-hub.db`。没有配置企业微信凭据时，除真实渠道投递与回调外的页面、API、提醒、插件和持久化流程仍可测试。

如需让企业微信 API 经过自建反向代理，在 `.env` 设置 `NOTIFY_HUB_WECOM_API_BASE_URL=https://proxy.example.com/wecom` 并重启。仅支持 HTTPS；代理可带路径前缀，且必须原样转发其下的 `cgi-bin/*`。代理会接触应用 Secret、Access Token 和消息内容，只能使用完全受信任的地址。

也可以手工初始化。以下命令均在仓库根目录执行；迁移和应用必须使用相同的工作目录，以确保操作同一个 `data/notify-hub.db`：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

另开终端启动前端：

```powershell
Set-Location frontend
npm ci
npm run dev
```

运行质量检查和测试：

```powershell
.\.venv\Scripts\ruff.exe format --check backend plugins
.\.venv\Scripts\ruff.exe check backend plugins
.\.venv\Scripts\mypy.exe backend/app plugins
.\.venv\Scripts\pytest.exe
Push-Location frontend
npm run lint
npm run typecheck
npm run test
npm run build
Pop-Location
```

## Docker 单实例部署

v0.3.0 使用单镜像、单容器和 SQLite；不得启动多个共享同一数据库的副本。本机需先安装 Docker，然后：

1. `deploy/docker-compose.yml` 已显式声明全部容器环境变量，不再使用 `env_file` 整包注入。仅在仓库根目录的 `.env` 中提供 Compose 需要替换的部署标识：`NOTIFY_HUB_PUBLIC_BASE_URL`、`NOTIFY_HUB_WECOM_CORP_ID`、`NOTIFY_HUB_WECOM_AGENT_ID` 和可选的 `NOTIFY_HUB_WECOM_API_BASE_URL`；该文件已被 Git 忽略。
2. 创建 `data`、`logs`、`media`、`secrets` 目录。
3. 在 `secrets` 中创建 `app_master_key`、`jwt_secret`、`wecom_secret`、`wecom_callback_token`、`wecom_callback_aes_key` 文件，并限制宿主机读取权限。生产环境的主密钥和 JWT Secret 均应使用独立的强随机值。
4. 先用 `config --quiet` 校验 Compose，再从仓库根目录构建并启动固定的本地镜像版本。不要使用不带 `--quiet` 的配置输出，避免部署变量进入终端日志。

Linux 宿主机还需确保 UID/GID `10001:10001` 可写 `data`、`logs`、`media`，且可只读访问 `secrets`；不要为了绕过权限问题把容器改成 root。

```powershell
docker compose -f deploy/docker-compose.yml config --quiet
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml ps
```

容器以非 root 用户运行，启动时先执行 Alembic 升级，迁移成功后才启动 API。服务绑定在 `127.0.0.1:8788`，生产环境应由同机可信 HTTPS 反向代理提供公网访问。Compose 只把五个 Secret 文件逐个只读挂载到 `/run/secrets`，不会把 Secret 值写进环境配置或镜像。`data`、`logs`、`media` 均为持久化挂载；升级前应使用 SQLite 在线备份方式备份数据库，并单独离线备份主加密密钥。

手工迁移或检查：

```powershell
docker compose -f deploy/docker-compose.yml run --rm notify-hub migrate
docker compose -f deploy/docker-compose.yml logs --tail 200 notify-hub
```

升级时先备份，记录当前镜像标签，再修改 Compose 中的固定版本并重建。若新版本迁移可向后兼容，可切回旧镜像；若迁移不可逆，必须同时停止容器、恢复升级前数据库备份，再用旧镜像启动。不要对生产数据库自动执行 Alembic downgrade。
