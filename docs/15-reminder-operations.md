# Reminder Center 运维手册

本文是 `docs/14-reminder-center-development-plan.md` Phase 8 的运维基线，适用于当前单实例、SQLite、单容器部署。安全与可靠性原则以 `docs/07-security-and-reliability.md` 为准，通用部署说明见 `docs/08-deployment-and-operations.md`。

本文只描述仓库中已存在的行为。标为“待实现”的项目目前没有可依赖的 CLI、API 或后台操作，不应以直接修改生产数据库代替。

## 1. 运行边界与值班原则

必须始终满足：

- 同一 SQLite 数据库只允许一个 Notify Hub 实例写入；
- 数据库、`media/`、`.env` 和主加密密钥分别纳入备份；
- 网络请求不放在数据库事务内；
- 备份、恢复、迁移和批量清理都要记录操作者、时间、版本、校验结果和回滚点；
- 不在生产库执行未经演练的写 SQL；
- 不把 `.env`、主加密密钥、历史 Action Token、企业微信 Secret 或回调原文写入工单和日志。

以下命令假定在仓库根目录执行，并使用 `deploy/docker-compose.yml`。实际部署若改过项目目录、服务名、数据库 URL 或挂载路径，应先以 `docker compose ... config` 和容器内环境确认，不要直接套用示例路径。

## 2. 日常检查与故障分级

### 2.1 当前可用检查

```bash
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs --tail 200 notify-hub
curl --fail --silent http://127.0.0.1:8788/health/live
curl --fail --silent http://127.0.0.1:8788/health/ready
```

`live` 只证明进程能响应；`ready` 检查数据库、Alembic revision 和 Delivery Worker 心跳。`ready` 失败时不得继续接收需要可靠持久化的新事件，应先查看返回的 `checks` 和容器日志。

### 2.2 建议分级

| 级别 | 例子 | 首要动作 |
| --- | --- | --- |
| P1 | 数据库损坏、恢复后 Secret 无法解密、提醒持续丢失或大面积重复发送 | 停止入口与 Worker，保全现场，切换到已验证回滚点 |
| P2 | `ready` 持续失败、Worker 心跳过期、dead/retry 队列持续增长、SQLite 锁错误持续出现 | 暂停变更，检查数据库和租约，评估是否需要回滚 |
| P3 | 单条永久失败、单个非法回调、单个媒体缺失 | 隔离单条记录，按审计流程重试或修正配置 |

## 3. SQLite 一致备份

### 3.1 备份集合

一次可恢复备份至少包含：

1. SQLite 数据库；
2. 需要长期保留的 `media/` 文件；
3. 固定镜像版本或 Git commit；
4. `.env` 的受控备份；
5. 与数据库分开保存的主加密密钥；
6. 文件清单、大小、SHA-256、备份时间和操作者。

只有数据库而没有原主加密密钥时，数据库中的加密 Secret 无法恢复。备份文件必须加密，并限制读取权限。

### 3.2 在线数据库备份

运行中的 WAL 数据库不得只复制 `notify-hub.db` 文件。应使用 SQLite Online Backup API。下面的命令在容器内创建一致快照并立即执行校验；默认数据库路径来自当前 Compose 挂载：

```bash
docker compose -f deploy/docker-compose.yml exec -T notify-hub python - <<'PY'
import datetime
import hashlib
import pathlib
import sqlite3

source_path = "/app/data/notify-hub.db"
backup_dir = pathlib.Path("/app/data/backups")
backup_dir.mkdir(parents=True, exist_ok=True)
stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
backup_path = backup_dir / f"notify-hub-{stamp}.db"

source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
target = sqlite3.connect(backup_path)
try:
    source.backup(target)
    quick_check = target.execute("PRAGMA quick_check").fetchone()[0]
    foreign_key_errors = target.execute("PRAGMA foreign_key_check").fetchmany(1)
finally:
    target.close()
    source.close()

if quick_check != "ok" or foreign_key_errors:
    backup_path.unlink(missing_ok=True)
    raise SystemExit("backup validation failed")

with backup_path.open("rb") as handle:
    digest = hashlib.file_digest(handle, "sha256").hexdigest()
print(f"backup={backup_path} sha256={digest}")
PY
```

若 `NOTIFY_HUB_DATABASE_URL` 指向其他路径，必须替换 `source_path`。备份任务应由宿主机或外部监控确认，不能只依赖 Notify Hub 自己发送“备份成功”。

数据库快照完成后，再备份媒体和配置。媒体备份时间窗内新增的文件可能晚于数据库快照；恢复时允许存在未被数据库引用的多余文件，但不允许数据库引用的长期媒体缺失。因此推荐顺序为：先同步一次媒体、执行数据库在线备份、再增量同步一次媒体，并为最终集合生成校验清单。

### 3.3 备份后检查

- `PRAGMA quick_check` 返回 `ok`；
- `PRAGMA foreign_key_check` 无结果；
- SHA-256 与备份清单一致；
- 备份文件能在隔离目录以只读方式打开；
- 固定镜像版本和 Alembic revision 已记录；
- 主密钥备份可取回，但未和数据库放在同一介质；
- 最近一次恢复演练未超过 90 天。

## 4. 恢复演练与生产恢复

### 4.1 隔离恢复演练

至少在首次发布前、数据库迁移后以及每 90 天执行一次：

1. 新建隔离目录，不挂载生产 `data/`、`media/` 或端口；
2. 校验数据库和媒体备份的 SHA-256；
3. 放入备份数据库、媒体、配置和原主加密密钥；
4. 先用备份记录的同版本镜像启动；
5. 执行 `PRAGMA quick_check` 和 `PRAGMA foreign_key_check`；
6. 核对 Alembic current revision；
7. 如演练升级，再执行 `migrate` 到目标 head；
8. 验证管理员登录、Secret 解密状态、提醒定义、Occurrence、待投递队列、插件游标和回调去重记录；
9. 使用测试接收人发送通知，并确认待投递任务能继续；
10. 重启一次，确认调度不漂移、不补发无界历史实例、不重复生成同一 Occurrence；
11. 销毁演练环境中的 Secret 副本并保存脱敏结果。

### 4.2 生产恢复

生产恢复必须先停止唯一实例，确认没有第二个容器仍指向同一数据库。保留故障现场的数据库、`-wal`、`-shm` 和媒体目录，不要覆盖唯一副本。

推荐顺序：

1. `docker compose -f deploy/docker-compose.yml stop notify-hub`；
2. 将当前数据库、WAL/SHM 和媒体目录移动到带时间戳的只读隔离目录；
3. 将已校验的备份数据库恢复为配置中的数据库路径；
4. 恢复匹配的媒体、`.env` 和原主加密密钥；
5. 先用备份时的固定镜像版本启动并检查数据；
6. 需要升级时再按第 5 节执行迁移；
7. 检查 `ready`、Worker 心跳、待投递数和最近提醒；
8. 先向测试接收人发送一条通知，再恢复公网入口；
9. 观察至少一个完整提醒和重试周期。

禁止在服务运行时用文件复制覆盖数据库；禁止只恢复 `.db` 而混用旧 WAL/SHM；禁止在未验证主密钥的情况下轮换密钥来“修复”解密错误。

## 5. 迁移、发布和回滚

### 5.1 发布前

```bash
docker compose -f deploy/docker-compose.yml config --quiet
docker compose -f deploy/docker-compose.yml run --rm --entrypoint sh notify-hub -c 'cd /app/backend && alembic current'
docker compose -f deploy/docker-compose.yml run --rm --entrypoint sh notify-hub -c 'cd /app/backend && alembic heads'
```

随后：

- 固定目标镜像 tag/digest，不使用不可追踪的 `latest` 作为正式发布依据；
- 阅读迁移脚本的 `upgrade()` 和 `downgrade()`；
- 在生产数据副本完成一次从 current 到 head 的演练；
- 创建并校验第 3 节备份；
- 记录迁移前 revision、镜像版本、队列计数和回滚负责人。

### 5.2 向前迁移

容器正常入口会在启动前自动执行 `alembic upgrade head`。正式升级更推荐在应用停止后显式执行，以便把迁移失败与应用启动失败分开：

```bash
docker compose -f deploy/docker-compose.yml stop notify-hub
docker compose -f deploy/docker-compose.yml run --rm notify-hub migrate
docker compose -f deploy/docker-compose.yml up -d notify-hub
curl --fail --silent http://127.0.0.1:8788/health/ready
```

迁移后再次记录 `alembic current`，检查日志、核心表计数、外键、Worker 心跳，并执行一条测试通知。

### 5.3 回滚

生产默认回滚方案是“旧固定镜像 + 迁移前完整备份”，而不是直接执行破坏性 downgrade。

仅当以下条件全部满足时，才可考虑 `alembic downgrade <revision>`：

- 对应 `downgrade()` 已在生产数据副本验证；
- 不会丢弃发布后产生且必须保留的数据；
- 已停止应用和 Worker；
- 已再次创建故障现场备份；
- 变更负责人明确批准。

否则停止新版本，恢复迁移前数据库和匹配媒体，再启动旧镜像。回滚后同样要验证 `ready`、Secret 解密、待投递队列、提醒调度和回调幂等。

## 6. Worker 崩溃与租约恢复

### 6.1 已实现行为

| Worker/记录 | 当前恢复机制 | 运维含义 |
| --- | --- | --- |
| Delivery | `processing` 带租约；每轮回收已过期租约到 `pending`；默认租约由 `delivery_lease_seconds` 控制 | 硬崩溃后等待租约过期，任务会重新 claim |
| Reminder Planner | 基于数据库 due 状态和条件更新 claim；Occurrence 有唯一键 | 重复扫描应保持幂等，重启后从数据库继续 |
| Reminder Escalation | OccurrenceRecipient 带 claim 与超时 | 催办失败或崩溃后由过期 claim 恢复，不应提前消耗发送次数 |
| InteractionEvent | `processing` 带 2 分钟 claim；过期记录可再次 claim | 未知异常按最大次数重试，耗尽进入 `dead` |
| IncomingMessage | 启动时将残留 `processing` 重置为 `pending` | 单实例重启后重新处理；回调去重键必须防止重复入库 |

企业微信已收到消息但响应在本地落库前丢失时，仍存在重复投递窗口。不能通过“发送前先标 succeeded”消除该窗口，否则会造成消息丢失。

### 6.2 崩溃处理步骤

1. 确认只有一个实例；
2. 保存崩溃前后日志，记录 delivery/reminder/occurrence/interaction ID；
3. 检查 `/health/ready` 和 Delivery Worker 心跳；
4. 重启同一固定版本；
5. 等待至少一个最长租约周期；
6. 观察 `processing` 是否回到可处理状态、attempt 是否增加、Occurrence 是否保持唯一；
7. 若任务反复失败，按第 7 节处理，不要直接清空 claim 字段；
8. 若出现重复发送，核对 provider response、attempt、event key 和是否曾运行第二实例。

可重复执行的自动化验证：

```bash
python -m pytest backend/tests/test_delivery_worker.py backend/tests/test_reminder_integration.py
```

正式发布还应在隔离环境执行硬终止演练：分别在 Delivery 已 claim、Reminder 正在 emit、Interaction 已 claim 时终止容器，再启动并验证无丢失、Occurrence 不重复、回调副作用幂等。该演练不得对真实接收人执行。

## 7. Delivery 与 Interaction 失败处理

### 7.1 Delivery dead

Delivery 会记录每次 `DeliveryAttempt`。可重试错误按退避重试，达到 `max_attempts` 或遇到永久错误后进入 `dead`。

处理顺序：

1. 在通知详情查看 error code、error message 和 attempts；
2. 先修复根因，例如接收人身份、应用可见范围、渠道 Secret、代理或媒体；
3. 只对确认仍应发送的单条 dead Delivery 使用后台“手工重试”；
4. 重试会通过现有管理员 API 重新置为 `pending`，并写 `delivery.retry` 审计；
5. 观察新 attempt 和最终状态；
6. 不对永久业务错误循环重试，不批量重放未知范围的 dead。

当前存在的接口是 `POST /api/v1/admin/deliveries/{delivery_id}/retry`，只接受 `dead` 状态。优先使用后台页面，避免手工构造管理员凭据。

### 7.2 InteractionEvent

InteractionEvent 终态包括 `processed`、`rejected` 和 `dead`；权限失败进入 `rejected`，未知异常按 `max_attempts` 有界重试并记录稳定错误类型。管理员可通过提醒维护 API 审计重放 `dead` Interaction；`failed` IncomingMessage 也有独立重试 API，二者不会混用。

### 7.3 SQL 只读诊断

以下查询只用于备份副本或以 SQLite `mode=ro` 打开的生产库；它们不是修复命令：

```sql
SELECT status, COUNT(*) FROM deliveries GROUP BY status ORDER BY status;

SELECT id, status, attempt_count, max_attempts, last_error_code,
       next_attempt_at, claimed_by, claim_expires_at
FROM deliveries
WHERE status IN ('dead', 'retry_wait', 'processing')
ORDER BY updated_at
LIMIT 200;

SELECT status, COUNT(*) FROM interaction_events GROUP BY status ORDER BY status;

SELECT id, status, action, received_at, processed_at, claimed_by, claim_expires_at
FROM interaction_events
WHERE status IN ('pending', 'processing', 'rejected')
ORDER BY received_at
LIMIT 200;
```

查询结果可能包含用户标识，不得粘贴到公开工单。

## 8. 数据保留、批量清理与媒体 GC

### 8.1 已实现媒体清理

Media Cleanup Worker 每小时扫描一次 `expires_at <= now` 的 MediaAsset，默认每批最多 100 条；文件删除失败时保留记录供后续重试。它适合清理明确标为临时且已过期的媒体。

媒体清理会排除 Reminder 定义与 Occurrence snapshot 正在引用的资源；API 删除也执行相同引用保护。长期提醒所需媒体仍必须纳入备份，不能只依赖渠道侧临时 media ID。

自动化边界测试：

```bash
python -m pytest backend/tests/test_media_cleanup_worker.py backend/tests/test_public_media.py backend/tests/test_media_security.py
```

### 8.2 通用历史清理

提醒维护 API 提供带 `before`、`limit` 和默认 `dry_run=true` 的有界清理，当前只清理终态 InteractionEvent、终态 IncomingMessage 和终态 ReminderDraft。Event、Notification、DeliveryAttempt、审计日志和 Occurrence 作为业务历史保留，不会被维护任务隐式删除。

实现批量清理时必须具备：dry-run、明确 cutoff、稳定主键分页、小批次事务、每批暂停、删除上限、审计、可中止、备份点和失败续跑；先删叶子记录，再按领域保留策略处理父记录。

### 8.3 孤立媒体安全流程

**待实现：** 文件系统孤立文件扫描、数据库引用汇总和隔离区清理尚无 CLI/API。正式 GC 应按以下顺序实现和验收：

1. 对数据库与媒体目录创建一致备份和清单；
2. 只读收集 Reminder、Notification、Occurrence snapshot、ChannelMediaCache 等全部引用；
3. 计算候选集合，不在扫描阶段删除；
4. 排除近期创建、上传中、待投递、retry_wait、processing 和未结束提醒使用的媒体；
5. 输出 dry-run 清单、大小、checksum 和引用证据；
6. 将候选移动到同文件系统隔离区，而非立即永久删除；
7. 保留至少一个完整提醒周期并验证历史详情、重试和渠道媒体重新上传；
8. 小批量永久删除隔离文件并记录审计；
9. 任一步发现引用即撤销该候选。

数据库有记录但文件丢失与“孤立文件”是两类问题：前者应从备份恢复或让 Delivery 明确失败，不能由 GC 静默删除数据库证据。

## 9. SQLite 锁竞争和长时间运行验证

### 9.1 当前保护

应用连接启用 `foreign_keys=ON`、WAL 和可配置 `busy_timeout`，业务 claim 使用短事务。SQLite 部署仍必须保持单实例；WAL 不是多实例 leader election。

低影响检查（前三项只读，`wal_checkpoint(PASSIVE)` 会尝试推进 checkpoint）：

```sql
PRAGMA journal_mode;
PRAGMA busy_timeout;
PRAGMA quick_check;
PRAGMA foreign_key_check;
PRAGMA wal_checkpoint(PASSIVE);
```

`wal_checkpoint(PASSIVE)` 不应作为每次请求操作。若 WAL 长期无法 checkpoint，应查找长时间读事务、备份进程、异常连接和持续写入，不要在高峰期直接执行阻塞式 truncate checkpoint。

### 9.2 发布前压力与耐久测试

在与生产相同文件系统和 SQLite 配置的隔离环境运行至少 24 小时，正式发布前建议 72 小时。测试同时包含：

- Event API 的正常请求和稳定 event_key 重试；
- Once、Interval、Cron、misfire 和持续催办；
- 企业微信 Adapter 的可控成功、超时、可重试和永久失败替身；
- Interaction 重复回调和权限失败；
- 每小时媒体清理；
- 在线备份；
- 周期性优雅重启和至少一次硬终止；
- 管理端列表、详情和只读统计并发访问。

每轮记录请求量、成功率、P95/P99 延迟、`database is locked` 次数、WAL 大小、checkpoint 结果、各状态队列深度、最老任务年龄、Worker 心跳、重复 Occurrence 和重复副作用。

通过标准：

- 无数据损坏、外键错误和未解释的锁错误；
- `quick_check=ok`；
- 重启后没有丢失提醒或无界历史补发；
- 同一 reminder + occurrence key 不重复；
- 重复回调不重复完成；
- 过期租约在预期窗口回收；
- dead 数量与注入的永久失败相符；
- 备份可在隔离环境恢复；
- WAL 在负载降低后能回落；
- 全部质量门禁通过。

**待实现：** 仓库当前没有统一的长时间压测脚本和结果归档格式。首次正式发布必须补齐可重复执行的负载工具或测试 harness，不能只以短时 pytest 代替耐久验证。

## 10. 指标与告警

### 10.1 当前能力

当前有结构化日志、`/health/live`、`/health/ready`、Delivery Worker heartbeat，以及管理员提醒指标端点 `/api/v1/admin/reminders/metrics`，覆盖活动提醒/实例、待处理接收人、Delivery、Interaction dead 和失败消息。Prometheus exporter 与更多延迟直方图可按部署监控栈后续接入。

在指标端点完成前，外部监控至少探测 `ready`，采集容器日志、重启次数、CPU/内存、数据库/WAL/媒体目录大小和磁盘剩余空间。只读 SQL 统计可低频执行，但不能用高频全表扫描代替指标。

### 10.2 推荐指标

- `notify_hub_ready`；
- Delivery Worker heartbeat age；
- deliveries 按 `pending/processing/retry_wait/dead/succeeded/cancelled` 的数量；
- pending/retry_wait 最老任务年龄；
- delivery attempts 成功率、重试率、永久失败率和发送延迟；
- reminder planner/escalation scan lag；
- active Reminder、pending Occurrence、overdue Occurrence 数；
- Interaction 按状态数量、最老 processing claim age、重复/拒绝计数；
- SQLite busy/locked 错误、事务耗时、数据库/WAL 大小；
- 媒体总量、过期候选、清理成功/失败和孤立候选；
- 备份时间、时长、大小、校验结果和最近恢复演练时间。

所有 label 必须低基数；不要把 reminder ID、delivery ID、用户 ID 或 error message 作为指标 label，它们应留在脱敏日志中。

### 10.3 初始告警阈值

以下是个人/家庭单实例部署的初始值，上线两周后应按基线调整：

| 信号 | Warning | Critical |
| --- | --- | --- |
| `/health/ready` | 连续 2 次失败或 2 分钟不可用 | 5 分钟不可用 |
| Delivery heartbeat age | 超过配置 TTL | 超过 2 倍 TTL |
| 最老 pending/retry_wait | 超过 5 分钟 | 超过 15 分钟 |
| dead 增量 | 15 分钟内新增 1 条 | 15 分钟内新增 5 条或持续增长 |
| Interaction processing | 超过 2 分钟租约仍未恢复 | 超过 10 分钟或反复 claim |
| SQLite busy/locked | 5 分钟内出现 1 次 | 5 分钟内 5 次或任何请求失败 |
| 磁盘剩余 | 低于 20% | 低于 10% 或不足一次完整备份空间 |
| WAL 大小 | 超过数据库大小或连续增长 30 分钟 | 超过数据库 2 倍且无法 PASSIVE checkpoint |
| 数据库备份 | 24 小时无成功备份 | 48 小时无成功备份或校验失败 |
| 恢复演练 | 超过 75 天 | 超过 90 天 |

低流量环境不适合只看百分比；成功率告警必须同时设置最小样本量。

## 11. 正式发布验收清单

### 架构与部署

- [ ] 单实例写入 SQLite，未运行共享数据库的第二副本；
- [ ] 镜像使用固定 tag/digest；
- [ ] HTTPS、反向代理、请求体限制和企业微信回调地址正确；
- [ ] 数据库与媒体挂载已核对，容器非 root，日志轮转已启用；
- [ ] `.env` 和主加密密钥权限正确且未入库。

### 数据与迁移

- [ ] 空库升级到 head 通过；
- [ ] 当前正式 revision 升级到 head 的副本演练通过；
- [ ] `quick_check=ok`，`foreign_key_check` 无结果；
- [ ] 迁移前一致备份、SHA-256 和回滚点已记录；
- [ ] 旧镜像与迁移前备份可用；
- [ ] 隔离恢复演练验证 Secret、提醒、队列、媒体和插件游标。

### 可靠性

- [ ] Delivery、Reminder Planner、Escalation 和 Interaction 崩溃/租约恢复通过；
- [ ] 重启不丢提醒，不重复生成 Occurrence，不无界补发；
- [ ] 菜单事件重复投递幂等，历史 Action 重复点击幂等；
- [ ] 交互式文字/图文/图片消息在企业微信和个人微信中的企业微信插件均可展示；
- [ ] 【快捷操作】中的五种菜单事件均能按 UserID 操作最近一次成功发送的交互式提醒；
- [ ] 普通通知、系统消息和操作确认消息不更新最近交互提醒指针，新指针覆盖旧指针且目标失效后不回退；
- [ ] 每种菜单操作的结果消息均包含目标任务名称；
- [ ] Delivery dead 可查询、可审计重试；
- [ ] Interaction dead/replay 能力已实现，或被明确列为发布阻断项；
- [ ] 通用历史清理具备 dry-run、边界批次、审计和续跑；
- [ ] 引用感知媒体 GC 和隔离期流程通过；
- [ ] 24/72 小时锁竞争与耐久测试通过。

### 监控与安全

- [ ] 外部监控覆盖 live、ready、容器重启、磁盘和备份；
- [ ] 队列深度、最老任务、Worker heartbeat、锁错误和媒体容量可观测；
- [ ] 告警通知不只依赖 Notify Hub 自身；
- [ ] 日志和审计不含 Secret、历史 Action Token、完整回调或敏感正文；
- [ ] 管理员重试、取消、清理、备份和恢复均有审计记录；
- [ ] 发布后观察至少一个完整 Once/Interval/Cron 和持续催办完成周期。

### 质量门禁

```bash
python -m ruff format --check backend plugins
python -m ruff check backend plugins
python -m mypy backend/app plugins
python -m pytest
cd frontend
npm run lint
npm run typecheck
npm run test
npm run build
```

Phase 8 只有在本清单全部满足、待实现项不再作为发布阻断项、恢复演练证据和耐久测试结果均归档后，才可标记完成。
