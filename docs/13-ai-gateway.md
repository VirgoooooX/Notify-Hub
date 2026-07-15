# Notify Hub AI Gateway

## 边界

平台管理员维护 Provider 和 Profile。插件通过 `context.ai` 调用稳定 Profile，不接触 API Key、Base URL、模型或任意鉴权头。AI 只返回结构化建议，插件和核心的确定性代码负责过滤、emit、幂等和 checkpoint。

## 控制平面

- AI Provider：预置类型、兼容协议、Base URL、TLS/私网策略、超时、重试和结构化输出模式。
- Provider 模型目录：管理员主动从兼容 `/models` 端点同步；新发现模型默认不可用，必须显式勾选进入允许列表。后续同步中消失的模型会自动退出允许列表。
- AI Profile：可复用的运行策略，定义稳定 ID、单一能力类型、Provider、从允许列表选择的模型、Temperature、输出上限、超时、输出语言、推理强度、详细程度、理由开关/长度、补充系统约束、缓存 TTL、每日请求和 Token 上限；API 和调用执行时都会重新验证模型权限与能力匹配。
- API Key：使用 SecretStore 的 `scope_type=ai_provider`、`scope_id=<provider_id>`、`name=api_key`；只显示是否配置。
- 调用历史：保存输入哈希、缓存命中、状态、延迟、Token 和稳定错误码，不保存原文或 Prompt。

Provider 可在控制台编辑端点、TLS/私网策略、启用状态、超时、重试和结构化输出模式；API Key 独立更新且永不回显。删除 Provider 使用软删除以保留历史审计，删除时清理 API Key 并从管理列表和运行时隐藏；若仍有未删除 Profile 引用则返回冲突，管理员必须先迁移或删除这些 Profile。

Profile 的补充系统约束只能收窄行为，不能覆盖平台安全规则。防提示注入、不执行内容中的指令、禁用工具、结构化输出与 Schema 校验由 Gateway 固定执行，不设计为可关闭开关。具体业务判断、标签、字段和业务 Prompt 留在插件中，避免 Profile 与某个插件耦合。

删除 Profile 使用软删除：管理列表和运行时立即视为不存在，但保留历史调用关联。启用中的插件仍引用该 Profile 时返回冲突，管理员必须先切换 Profile、改为规则模式或停用插件。插件保存配置及重新启用时也会复核 Manifest/Profile 依赖，已删除或停用的 Profile 不能重新形成悬空引用。

## 数据面

第一版提供 `classify`、`extract`、`summarize`，统一调用 `{base_url}/chat/completions`。三类结果都使用结构化模型，按 `json_schema -> json_object -> prompt_json` 降级；某一级修复一次仍无效时继续尝试下一级。响应必须通过 Pydantic 严格校验；只额外兼容整个响应均被 Markdown JSON 围栏包裹的情况，不从任意说明文字中提取 JSON。429、5xx、网络和超时按 Provider 重试上限处理，永久错误不重复重试。

Provider URL 默认 HTTPS、禁止 URL 凭据和 fragment、禁止云元数据地址，并校验 DNS 与实际连接 peer。私网访问必须由管理员显式开启；重定向默认拒绝。

## Codex X Monitor

推荐 `rules_then_ai`：只处理游标之后的原创帖子，回复和转推在 AI 前过滤；确定性规则同时给出通知/忽略决策和规则置信度，只有低于插件 `rule_ai_threshold` 的暧昧文本才进入 AI，并以最多五条批量分类。明确公告和明确否定/询问由高置信度规则直接处理。首次 baseline、无增量、高置信度规则结果和缓存命中不调用 Provider。

AI 返回 `ignore` 时推进 checkpoint；返回 `notify` 且达到置信度后才 emit。AI 或结构化校验失败时 fail-closed，不推进候选游标。Event emit 失败时 AI 结果已持久缓存，下次运行不会重复付费。

## 无凭据运行

没有 Provider 或 API Key 时不会在启动阶段发起 AI 请求。核心事件、提醒、投递、管理后台和使用 `decision_mode=rules` 的插件保持可用。本地 LM Studio、vLLM 或 Ollama 等无需 Key 的兼容端点可在管理员显式允许私网后使用。

可选的 `NOTIFY_HUB_AI_ENABLED` 与 `NOTIFY_HUB_AI_BOOTSTRAP_*` 变量只在数据库没有 Provider 时创建一个默认 Provider 和 `semantic_classifier_fast` Profile；已有数据库配置永不覆盖。API Key 为空时可稍后在后台填写；非空时必须同时配置 `NOTIFY_HUB_SECRET_ENCRYPTION_KEY`，并写入 SecretStore。
