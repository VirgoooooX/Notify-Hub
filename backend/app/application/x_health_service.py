from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.application.event_service import EventService
from app.application.x_source_service import (
    XHealthTarget,
    XSourceFetchResult,
    XSourceService,
)
from app.config import Settings
from app.domain.clock import Clock
from app.domain.x_source import XPostRecord, XSourceAccountError, XSourceError
from app.infrastructure.database.base import new_id
from app.infrastructure.database.models import XSourceHealth
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class XHealthTargetProvider(Protocol):
    async def __call__(self) -> list[XHealthTarget]: ...


@dataclass(frozen=True, slots=True)
class _FetchResult:
    target: XHealthTarget
    posts: list[XPostRecord]
    error: XSourceError | None = None
    degraded_error: XSourceError | None = None
    provider_used: str | None = None


@dataclass(frozen=True, slots=True)
class _Decision:
    scope_key: str
    scope_type: str
    kind: str
    incident_id: str | None
    username: str | None
    error_code: str | None = None
    error_message: str | None = None
    latest_post_at: datetime | None = None
    consecutive_failures: int = 0
    provider_used: str | None = None


class XHealthService:
    """Checks X targets independently of plugin runs and emits durable alerts."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        event_service: EventService,
        source: XSourceService,
        targets: XHealthTargetProvider,
        settings: Settings,
    ) -> None:
        self._factory = factory
        self._clock = clock
        self._events = event_service
        self._source = source
        self._targets = targets
        self._settings = settings

    async def run_once(self) -> int:
        targets = await self._targets()
        if not targets:
            return 0
        results = [await self._fetch(target) for target in targets]
        all_failed = all(result.error is not None for result in results)
        source_errors = [
            result.error
            for result in results
            if result.error is not None and not isinstance(result.error, XSourceAccountError)
        ]
        degraded_results = [result for result in results if result.degraded_error is not None]
        # A single account-level 404 must not page the whole provider. If every
        # target failed, however, any provider-level failure in the batch is
        # evidence that the shared source is unhealthy, even when another target
        # independently returned an account-level error.
        source_failed = all_failed and bool(source_errors)
        source_degraded = bool(degraded_results) and not source_failed
        decisions: list[_Decision] = []
        for result in results:
            decisions.append(await self._record_account(result, suppress_alert=source_failed))

        if source_failed:
            first_error = source_errors[0]
            assert first_error is not None
            source_decision = await self._record_source_failure(first_error)
            decisions.append(source_decision)
        elif source_degraded:
            degraded_error = degraded_results[0].degraded_error
            assert degraded_error is not None
            source_decision = await self._record_source_degraded(degraded_error)
            decisions.append(source_decision)
        else:
            source_decision = await self._record_source_success()
            decisions.append(source_decision)
            if source_decision.kind == "recovered":
                decisions = [
                    decision
                    for decision in decisions
                    if not (decision.scope_type == "account" and decision.kind == "recovered")
                ]

        emitted = 0
        for decision in decisions:
            if decision.kind == "none":
                continue
            if decision.kind == "content_recovered":
                title = "X 账号内容已恢复更新"
                content = f"@{decision.username} 已重新发现新的推文。"
                event_type = "system.x_account_content_recovered"
                level = "info"
            elif decision.kind == "content_stale":
                title = "X 账号长时间没有新推文"
                content = (
                    f"@{decision.username} 已超过配置的内容静默阈值未发现新推文；"
                    f"{self._source.provider_name} 请求正常，"
                    "可能是账号暂未更新或数据源滞后。"
                )
                event_type = "system.x_account_content_stale"
                level = "warning"
            elif decision.kind == "recovered" and decision.scope_type == "provider":
                title = "X 数据源已恢复"
                content = "X 数据源已连续成功获取，监控已恢复正常。"
                event_type = "system.x_source_recovered"
                level = "info"
            elif decision.kind == "degraded" and decision.scope_type == "provider":
                title = "X 主数据源异常，已自动切换 RSSHub"
                content = (
                    f"twscrape {self._error_summary(decision.error_code)}，"
                    "当前暂由 RSSHub 获取推文；请尽快检查 Cookie 或版本。"
                )
                event_type = "system.x_source_degraded"
                level = "warning"
            elif decision.scope_type == "provider":
                if decision.error_code == "x_sources_unavailable":
                    title = "X 数据源全部不可用"
                    content = (
                        "twscrape 和 RSSHub 都无法获取推文，X 监控暂时无法继续；"
                        "请检查网络、Cookie 和 RSSHub。"
                    )
                else:
                    title = f"X 数据源 {self._source.provider_name} 获取失败"
                    content = (
                        f"{self._source.provider_name} 连续获取失败 "
                        f"{decision.consecutive_failures} 次，"
                        "X 监控账号可能无法及时获取新推文。"
                    )
                event_type = "system.x_source_unavailable"
                level = "critical"
            elif decision.kind == "recovered":
                title = "X 数据源已恢复"
                content = f"@{decision.username} 的 X 推文获取已恢复正常。"
                event_type = "system.x_account_recovered"
                level = "info"
            else:
                title = "X 账号推文获取异常"
                content = f"无法正常获取 @{decision.username} 的推文。"
                event_type = "system.x_account_unavailable"
                level = "warning"

            if not self._settings.x_health_alert_enabled:
                continue
            recipients = self._settings.x_health_alert_recipient_ids
            if not recipients:
                continue
            event_key = self._event_key(decision)
            accept_result = await self._events.accept_internal_event(
                source_type="system",
                source_id="x_source_health",
                event_type=event_type,
                event_key=event_key,
                title=title,
                content=content,
                recipients=recipients,
                level=level,
                payload={
                    "provider": self._source.provider_name,
                    "scope_type": decision.scope_type,
                    "username": decision.username,
                    "error_code": decision.error_code,
                    "error_message": decision.error_message,
                    "consecutive_failures": decision.consecutive_failures,
                    "incident_id": decision.incident_id,
                    "provider_used": decision.provider_used,
                },
            )
            await self._mark_alert(decision, self._clock.now())
            if not accept_result.duplicate:
                emitted += 1
        return emitted

    async def _fetch(self, target: XHealthTarget) -> _FetchResult:
        try:
            fetch_with_status = getattr(self._source, "fetch_with_status", None)
            if fetch_with_status is None:
                posts = await self._source.fetch_records(
                    target.username,
                    limit=target.fetch_limit,
                    include_replies=target.include_replies,
                )
                return _FetchResult(
                    target=target,
                    posts=posts,
                    provider_used=self._source.provider_name,
                )
            result: XSourceFetchResult = await fetch_with_status(
                target.username,
                limit=target.fetch_limit,
                include_replies=target.include_replies,
            )
            return _FetchResult(
                target=target,
                posts=result.records,
                degraded_error=result.degraded_error,
                provider_used=result.provider_used,
            )
        except XSourceError as exc:
            return _FetchResult(target=target, posts=[], error=exc)
        except Exception as exc:
            # Provider implementations must normalize expected errors; this
            # guard keeps the supervisor alive if an adapter regresses.
            return _FetchResult(
                target=target,
                posts=[],
                error=XSourceError(f"unexpected X source error: {type(exc).__name__}"),
            )

    async def _record_source_degraded(self, error: XSourceError) -> _Decision:
        key = self._provider_key()
        async with self._factory() as session, session.begin():
            row = await self._get_or_create(session, key, scope_type="provider", username=None)
            now = self._clock.now()
            row.updated_at = now
            row.consecutive_failures += 1
            row.consecutive_successes = 0
            row.last_failure_at = now
            row.last_error_code = error.code
            row.last_error_message = str(error)[:500]
            if row.first_failure_at is None:
                row.first_failure_at = now
            if row.status != "incident":
                row.status = "incident"
                row.incident_id = new_id("xinc")
                row.incident_started_at = now
            if (
                row.last_alert_at is None
                or (now - row.last_alert_at).total_seconds()
                >= self._settings.x_health_repeat_interval_seconds
            ):
                return _Decision(
                    key,
                    "provider",
                    "degraded",
                    row.incident_id,
                    None,
                    error.code,
                    str(error)[:500],
                    None,
                    row.consecutive_failures,
                    "rsshub",
                )
            return _Decision(
                key,
                "provider",
                "none",
                row.incident_id,
                None,
                error.code,
                str(error)[:500],
                None,
                row.consecutive_failures,
                "rsshub",
            )

    async def _record_account(self, result: _FetchResult, *, suppress_alert: bool) -> _Decision:
        key = self._account_key(result.target.username)
        latest = max((post.published_at for post in result.posts), default=None)
        async with self._factory() as session, session.begin():
            row = await self._get_or_create(
                session,
                key,
                scope_type="account",
                username=result.target.username,
            )
            now = self._clock.now()
            row.updated_at = now
            if result.error is not None:
                row.consecutive_failures += 1
                row.consecutive_successes = 0
                row.last_failure_at = now
                row.last_error_code = result.error.code
                row.last_error_message = str(result.error)[:500]
                if row.first_failure_at is None:
                    row.first_failure_at = now
                if (
                    row.status != "incident"
                    and row.consecutive_failures >= self._settings.x_health_failure_threshold
                ):
                    row.status = "incident"
                    row.incident_id = new_id("xinc")
                    row.incident_started_at = now
                if suppress_alert or row.status != "incident":
                    return _Decision(
                        key,
                        "account",
                        "none",
                        row.incident_id,
                        result.target.username,
                        result.error.code,
                        str(result.error)[:500],
                        None,
                        row.consecutive_failures,
                    )
                if (
                    row.last_alert_at is None
                    or (now - row.last_alert_at).total_seconds()
                    >= self._settings.x_health_repeat_interval_seconds
                ):
                    return _Decision(
                        key,
                        "account",
                        "failure",
                        row.incident_id,
                        result.target.username,
                        result.error.code,
                        str(result.error)[:500],
                        None,
                        row.consecutive_failures,
                    )
                return _Decision(
                    key,
                    "account",
                    "none",
                    row.incident_id,
                    result.target.username,
                    result.error.code,
                    str(result.error)[:500],
                    None,
                    row.consecutive_failures,
                )

            was_incident = row.status == "incident"
            row.consecutive_failures = 0
            row.consecutive_successes += 1
            row.last_success_at = now
            row.last_error_code = None
            row.last_error_message = None
            if latest is not None and (row.last_post_at is None or latest > row.last_post_at):
                row.last_post_at = latest
            if (
                was_incident
                and row.consecutive_successes >= self._settings.x_health_recovery_success_threshold
            ):
                row.status = "healthy"
                decision = _Decision(
                    key,
                    "account",
                    "recovered",
                    row.incident_id,
                    result.target.username,
                    latest_post_at=latest,
                )
                row.incident_id = None
                row.incident_started_at = None
                row.first_failure_at = None
                return decision
            if row.status != "incident":
                row.status = "healthy"

            if not result.target.silence_enabled or row.last_post_at is None:
                return _Decision(
                    key,
                    "account",
                    "none",
                    row.incident_id,
                    result.target.username,
                    latest_post_at=latest,
                )
            age = (now - row.last_post_at).total_seconds()
            if age < result.target.silence_seconds:
                if row.stale_since is not None:
                    row.stale_since = None
                    row.stale_last_alert_at = None
                    return _Decision(
                        key,
                        "account",
                        "content_recovered",
                        row.incident_id,
                        result.target.username,
                        latest_post_at=latest,
                    )
                return _Decision(
                    key,
                    "account",
                    "none",
                    row.incident_id,
                    result.target.username,
                    latest_post_at=latest,
                )
            if row.stale_since is None:
                row.stale_since = now
            if (
                row.stale_last_alert_at is None
                or (now - row.stale_last_alert_at).total_seconds()
                >= self._settings.x_health_repeat_interval_seconds
            ):
                return _Decision(
                    key,
                    "account",
                    "content_stale",
                    row.incident_id,
                    result.target.username,
                    latest_post_at=latest,
                )
            return _Decision(
                key,
                "account",
                "none",
                row.incident_id,
                result.target.username,
                latest_post_at=latest,
            )

    async def _record_source_failure(self, error: XSourceError) -> _Decision:
        key = self._provider_key()
        async with self._factory() as session, session.begin():
            row = await self._get_or_create(session, key, scope_type="provider", username=None)
            now = self._clock.now()
            row.updated_at = now
            row.consecutive_failures += 1
            row.consecutive_successes = 0
            row.last_failure_at = now
            row.last_error_code = error.code
            row.last_error_message = str(error)[:500]
            if row.first_failure_at is None:
                row.first_failure_at = now
            immediate = bool(getattr(error, "alert_immediately", False))
            if row.status != "incident" and (
                immediate or row.consecutive_failures >= self._settings.x_health_failure_threshold
            ):
                row.status = "incident"
                row.incident_id = new_id("xinc")
                row.incident_started_at = now
            if row.status != "incident":
                return _Decision(
                    key,
                    "provider",
                    "none",
                    row.incident_id,
                    None,
                    error.code,
                    str(error)[:500],
                    None,
                    row.consecutive_failures,
                )
            if (
                row.last_alert_at is None
                or (now - row.last_alert_at).total_seconds()
                >= self._settings.x_health_repeat_interval_seconds
            ):
                return _Decision(
                    key,
                    "provider",
                    "failure",
                    row.incident_id,
                    None,
                    error.code,
                    str(error)[:500],
                    None,
                    row.consecutive_failures,
                )
            return _Decision(
                key,
                "provider",
                "none",
                row.incident_id,
                None,
                error.code,
                str(error)[:500],
                None,
                row.consecutive_failures,
            )

    async def _record_source_success(self) -> _Decision:
        key = self._provider_key()
        async with self._factory() as session, session.begin():
            row = await self._get_or_create(session, key, scope_type="provider", username=None)
            now = self._clock.now()
            row.updated_at = now
            was_incident = row.status == "incident"
            row.consecutive_failures = 0
            row.consecutive_successes += 1
            row.last_success_at = now
            row.last_error_code = None
            row.last_error_message = None
            if (
                was_incident
                and row.consecutive_successes >= self._settings.x_health_recovery_success_threshold
            ):
                decision = _Decision(key, "provider", "recovered", row.incident_id, None)
                row.status = "healthy"
                row.incident_id = None
                row.incident_started_at = None
                row.first_failure_at = None
                return decision
            if was_incident:
                return _Decision(key, "provider", "none", row.incident_id, None)
            row.status = "healthy"
            return _Decision(key, "provider", "none", row.incident_id, None)

    async def _get_or_create(
        self,
        session: AsyncSession,
        scope_key: str,
        *,
        scope_type: str,
        username: str | None,
    ) -> XSourceHealth:
        row = await session.get(XSourceHealth, scope_key)
        if row is None:
            row = XSourceHealth(
                scope_key=scope_key,
                scope_type=scope_type,
                provider=self._source.provider_name,
                username=username,
                status="healthy",
                consecutive_failures=0,
                consecutive_successes=0,
                created_at=self._clock.now(),
                updated_at=self._clock.now(),
            )
            session.add(row)
            await session.flush()
        return row

    async def _mark_alert(self, decision: _Decision, now: datetime) -> None:
        async with self._factory() as session, session.begin():
            row = await session.get(XSourceHealth, decision.scope_key)
            if row is None:
                return
            row.updated_at = now
            if decision.kind == "content_stale":
                row.stale_last_alert_at = now
            elif decision.kind in {"degraded", "failure", "recovered"}:
                row.last_alert_at = now

    def _event_key(self, decision: _Decision) -> str:
        if decision.kind == "recovered":
            return f"x-health:{decision.scope_key}:recovered:{decision.incident_id or 'unknown'}"
        if decision.kind == "content_recovered":
            return (
                f"x-health:{decision.scope_key}:content-recovered:"
                f"{int((decision.latest_post_at or self._clock.now()).timestamp())}"
            )
        if decision.kind == "content_stale":
            bucket = (
                int(self._clock.now().timestamp())
                // self._settings.x_health_repeat_interval_seconds
            )
            return f"x-health:{decision.scope_key}:content-stale:{bucket}"
        suffix = decision.incident_id or "pending"
        if decision.kind in {"degraded", "failure"}:
            bucket = (
                int(self._clock.now().timestamp())
                // self._settings.x_health_repeat_interval_seconds
            )
            return f"x-health:{decision.scope_key}:{decision.kind}:{suffix}:{bucket}"
        return f"x-health:{decision.scope_key}:incident:{suffix}"

    @staticmethod
    def _error_summary(error_code: str | None) -> str:
        return {
            "x_source_cookie_invalid": "Cookie 无效或已失效",
            "x_twscrape_incompatible": "版本与当前 X 页面不兼容",
            "x_twscrape_unavailable": "暂时不可用",
            "x_source_rate_limited": "触发了限流",
            "x_source_unavailable": "暂时不可用",
        }.get(error_code or "", "出现异常")

    def _account_key(self, username: str) -> str:
        return f"{self._source.provider_name}:account:{username.casefold()}"

    def _provider_key(self) -> str:
        return f"{self._source.provider_name}:provider"
