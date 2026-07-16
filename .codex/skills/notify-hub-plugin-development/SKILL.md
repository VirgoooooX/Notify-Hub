---
name: notify-hub-plugin-development
description: Develop, modify, debug, review, or test trusted Notify Hub plugins, including manifests, configuration schemas, PluginContext capabilities, event idempotency, durable cursors, media publishing, AI profiles, reminder creation, scheduling, fixtures, and plugin documentation. Use for work under plugins/builtin or plugins/private, plugin runtime integration, or requests to add a new monitor or data-source integration to Notify Hub.
---

# Notify Hub Plugin Development

Build plugins as event discoverers inside Notify Hub's trusted plugin runtime. Keep channel delivery, database access, retries, scheduling, and secrets in the platform.

## Start With Repository Context

1. Read the repository `AGENTS.md` and `PROJECT_GUIDE.md`.
2. If `.codegraph/` exists, use CodeGraph before broad text searches.
3. Read `docs/project-log.md` only when historical project context is useful, then inspect one similar built-in plugin.
4. Verify current contracts in `backend/app/plugin_runtime` and `backend/tests/test_plugin_runtime.py` before changing Manifest fields, `PluginContext`, media, AI, reminders, or runtime wiring.
5. Inspect the dirty worktree and preserve unrelated changes.

## Choose The Plugin Shape

- Put repository-maintained plugins in `plugins/builtin/<plugin_id>/`.
- Put administrator-deployed trusted plugins in `plugins/private/<plugin_id>/`.
- Use a stable lower-snake-case plugin ID and semantic version.
- Include `manifest.json`, implementation, Pydantic configuration, README, fixed fixtures, and tests.
- Reuse shared source models and helpers when behavior is genuinely common. Keep business matching rules inside the owning plugin.

## Implement In This Order

1. Define source-neutral input models and a validated configuration model with `extra="forbid"`.
2. Declare the minimum Manifest permissions and default schedule.
3. Fetch through `context.http` or an existing approved source adapter with explicit timeout and bounded responses.
4. Normalize source records before matching.
5. Load durable state and establish first-run baseline behavior.
6. Produce deterministic decisions and a stable source-derived `event_key`.
7. Call `context.emit_event()` and advance the current cursor only after `accepted` or `duplicate`.
8. Return bounded run metrics and write structured, non-sensitive logs.
9. Add fixtures and tests before changing runtime or UI integration.
10. Update the plugin README only for user-facing configuration or operation changes. Record durable engineering conclusions in the local project Log; update public architecture docs only when the accepted contract changes.

## Preserve Hard Boundaries

- Never import `app.channels.wecom` or construct channel payloads in a plugin.
- Never access ORM sessions, business tables, platform settings, or process environment directly.
- Never implement an internal scheduler, delivery retry queue, or random event key.
- Never store credentials in normal plugin configuration or log them.
- Never use `eval`, `exec`, `shell=True`, dynamic package installation, or import-time background tasks.
- Treat AI output as advice. Keep emit, dedupe, and cursor decisions deterministic.
- Keep network calls outside database transactions; the runtime owns persistence.

## Use Platform Capabilities

- **Static built-in image:** `context.media.public_static_url("asset.png")`.
- **Downloaded external cover:** `await context.media.publish_image_url(source_url)`; requires `media_write` and a network allowlist.
- **AI:** use the capability-specific `context.ai` method and declare matching `ai_profiles` or `ai_capabilities`.
- **Reminder creation:** use `context.reminders.create(...)`; declare explicit reminder permissions and recipient allowlists.
- **Secrets:** use `await context.get_secret(name)` and list the name in Manifest permissions.
- **State:** use `get_state`, `set_state`, or `save_checkpoint`; keep state bounded and versionable.

## Reliability Rules

- Default first run to baseline unless the product explicitly requires historical replay.
- Sort unstable source responses before processing.
- Checkpoint non-matches after successful scanning.
- For matches, checkpoint only after core acceptance or duplicate acknowledgement.
- Do not skip past a failed matching record.
- Bound recent-ID sets, summaries, payloads, fetch sizes, retries, and log fields.
- Preserve the same source-native ID across adapters so switching sources does not resend old events.

## Test The Risk Surface

Cover at least:

- configuration and Manifest validation;
- baseline and explicit history scan;
- new match, non-match, duplicate, and out-of-order records;
- stable event keys across reruns and source adapters;
- emit failure without cursor advancement;
- source timeout, parse failure, and rate limiting;
- permission denial for Secret, network, media, AI, or reminders;
- media fallback where applicable;
- log and payload Secret leakage;
- cancellation and plugin timeout.

Use fixed fixtures and fake contexts. Unit tests must not require live networks or providers.

## Validate

Run the narrow plugin tests first, then the affected runtime gates:

```powershell
.\.venv\Scripts\pytest.exe plugins\builtin\<plugin_id>\tests -q
.\.venv\Scripts\pytest.exe backend\tests\test_plugin_runtime.py -q
.\.venv\Scripts\ruff.exe format --check backend plugins
.\.venv\Scripts\ruff.exe check backend plugins
.\.venv\Scripts\mypy.exe backend\app plugins
```

Add broader tests when changing shared runtime, event, media, AI, reminder, or scheduling behavior.

## Review Checklist

- Manifest permissions match actual capability use.
- No channel, ORM, environment, or unrestricted network access leaks into the plugin.
- Event keys and cursor order are stable.
- Baseline does not emit historical records by default.
- Accepted and duplicate receipts are both safe checkpoints.
- Failure paths preserve replayability.
- Tests and README describe current behavior rather than planned behavior.
