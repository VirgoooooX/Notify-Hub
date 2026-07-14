from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

import httpx
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from app.api.errors import install_error_handlers
from app.api.health import router as health_router
from app.application.conversation_service import ConversationService
from app.application.event_service import EventService
from app.application.media_service import MediaService
from app.application.notification_service import NotificationService
from app.application.plugin_service import PluginService
from app.application.reminder_service import ReminderService
from app.application.runtime_adapters import (
    ConversationReplyEmitterAdapter,
    PluginEventEmitterAdapter,
    PluginSecretResolverAdapter,
    ReminderEventEmitterAdapter,
)
from app.application.speech_service import SpeechRecognitionService
from app.application.tts_media_service import TtsMediaService
from app.application.wecom_callback_service import WeComCallbackService
from app.application.wecom_media_service import (
    DatabaseMediaCacheRepository,
    OutboundWeComMediaService,
)
from app.channels.base import UnconfiguredChannel
from app.channels.wecom.adapter import WeComAdapter
from app.channels.wecom.client import WeComClient
from app.channels.wecom.crypto import WeComCrypto
from app.channels.wecom.media_adapter import WeComTemporaryMediaAdapter
from app.config import Settings, get_settings
from app.domain.clock import SystemClock
from app.infrastructure.database.models import PlatformSetting
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.logging.setup import configure_logging
from app.infrastructure.security.rate_limit import SlidingWindowLimiter
from app.infrastructure.security.request_id import new_request_id
from app.infrastructure.security.secret_store import SecretStore
from app.media.downloader import SafeMediaDownloader
from app.media.speech import LocalCommandAmrTranscoder, LocalCommandASR, LocalCommandTTS
from app.media.storage import MediaStorage
from app.plugin_runtime.registry import PluginRegistry
from app.workers.delivery_worker import DeliveryWorker
from app.workers.interaction_worker import InteractionWorker
from app.workers.media_cleanup_worker import MediaCleanupWorker
from app.workers.plugin_worker import PluginWorker
from app.workers.reminder_worker import ReminderWorker


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and "." not in path.rsplit("/", maxsplit=1)[-1]:
                return await super().get_response("index.html", scope)
            raise
        if response.status_code == 404 and "." not in path.rsplit("/", maxsplit=1)[-1]:
            return await super().get_response("index.html", scope)
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    clock = SystemClock()
    event_service = EventService(factory, clock)
    wecom_client = WeComClient(settings, clock)
    worker_stop = asyncio.Event()
    reminder_service = ReminderService(
        factory,
        ReminderEventEmitterAdapter(event_service),
        settings.jwt_secret.get_secret_value(),
    )
    conversation_service = ConversationService(
        factory, reminder_service, default_timezone=settings.app_timezone
    )
    reminder_worker = ReminderWorker(reminder_service)
    callback_service = WeComCallbackService(factory)
    media_http = httpx.AsyncClient(follow_redirects=False)
    media_storage = MediaStorage(settings.media_root)
    media_service = MediaService(
        media_storage,
        clock,
        downloader=SafeMediaDownloader(
            media_http,
            timeout_seconds=settings.media_download_timeout_seconds,
            max_redirects=settings.media_download_max_redirects,
        ),
        image_max_bytes=settings.media_image_max_bytes,
        voice_max_bytes=settings.media_voice_max_bytes,
        voice_max_seconds=settings.media_voice_max_seconds,
        retention_seconds=settings.media_retention_seconds,
    )
    temporary_media = WeComTemporaryMediaAdapter(
        wecom_client,
        DatabaseMediaCacheRepository(factory),
    )
    speech_recognition = (
        SpeechRecognitionService(LocalCommandASR(settings.asr_command))
        if settings.asr_command
        else None
    )
    tts_media_service = (
        TtsMediaService(
            LocalCommandTTS(settings.tts_command),
            LocalCommandAmrTranscoder(settings.transcode_command),
            media_service,
        )
        if settings.tts_command and settings.transcode_command
        else None
    )

    async def transcribe_wecom_voice(media_id: str) -> str:
        if speech_recognition is None:
            raise RuntimeError("ASR adapter is not configured")
        draft = await speech_recognition.recognize(
            None,
            download_voice=lambda: temporary_media.download_voice(
                media_id,
                max_bytes=settings.media_voice_max_bytes,
                max_seconds=settings.media_voice_max_seconds,
            ),
        )
        return draft.text

    interaction_worker = InteractionWorker(
        factory,
        reminder_service,
        conversation_service,
        ConversationReplyEmitterAdapter(factory, event_service),
        transcribe_wecom_voice if speech_recognition is not None else None,
    )
    outbound_media = OutboundWeComMediaService(
        factory,
        media_storage,
        temporary_media,
    )
    channel = (
        WeComAdapter(wecom_client, settings, outbound_media)
        if settings.wecom_corp_id
        else UnconfiguredChannel()
    )

    async def prepare_tts(text: str) -> str:
        if tts_media_service is None:
            raise RuntimeError("TTS adapter is not configured")
        async with factory() as session:
            asset = await tts_media_service.create_voice(
                session, text, created_by="delivery-worker"
            )
            return asset.id

    worker = DeliveryWorker(
        factory,
        channel,
        clock,
        "delivery-main",
        settings.delivery_lease_seconds,
        prepare_tts=prepare_tts,
        action_token_for_id=reminder_service.action_token,
    )
    media_cleanup = MediaCleanupWorker(factory, media_storage, clock)
    secret_store = (
        SecretStore(
            factory,
            clock,
            settings.secret_encryption_key.get_secret_value(),
        )
        if settings.secret_encryption_key is not None
        else None
    )
    plugin_service = PluginService(
        session_factory=factory,
        registry=PluginRegistry(),
        event_emitter=PluginEventEmitterAdapter(event_service),
        secret_resolver=PluginSecretResolverAdapter(secret_store),
        clock=clock.now,
    )
    plugin_worker = PluginWorker(plugin_service, worker_id="plugin-main")

    async def interaction_loop() -> None:
        while not worker_stop.is_set():
            try:
                await interaction_worker.run_once()
            except Exception as exc:
                structlog.get_logger().exception(
                    "interaction_worker_iteration_failed", error_type=type(exc).__name__
                )
            with suppress(TimeoutError):
                await asyncio.wait_for(worker_stop.wait(), timeout=1.0)

    async def media_cleanup_loop() -> None:
        while not worker_stop.is_set():
            await media_cleanup.run_once()
            with suppress(TimeoutError):
                await asyncio.wait_for(worker_stop.wait(), timeout=3600.0)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with factory() as session:
            persisted_settings = {
                row.key: row.value
                for row in await session.scalars(
                    select(PlatformSetting).where(
                        PlatformSetting.key.in_(["timezone", "retention_days"])
                    )
                )
            }
        timezone = persisted_settings.get("timezone")
        if isinstance(timezone, str):
            conversation_service.set_timezone(timezone)
        retention_days = persisted_settings.get("retention_days")
        if isinstance(retention_days, int):
            media_service.retention_seconds = retention_days * 86400
        await plugin_service.initialize()
        await interaction_worker.recover_stale_messages()
        if settings.environment != "test":
            await worker.heartbeat()
        app.state.ready = True
        tasks: list[asyncio.Task[None]] = []
        if settings.environment != "test":
            tasks.extend(
                [
                    asyncio.create_task(
                        worker.run(worker_stop, settings.worker_poll_interval_seconds),
                        name="delivery-worker",
                    ),
                    asyncio.create_task(
                        reminder_worker.run(worker_stop, settings.reminder_poll_seconds),
                        name="reminder-worker",
                    ),
                    asyncio.create_task(interaction_loop(), name="interaction-worker"),
                    asyncio.create_task(media_cleanup_loop(), name="media-cleanup-worker"),
                ]
            )
            await plugin_worker.start()
        yield
        app.state.ready = False
        worker_stop.set()
        if settings.environment != "test":
            await plugin_worker.stop()
        if tasks:
            await asyncio.gather(*tasks)
        await wecom_client.close()
        await media_http.aclose()
        await engine.dispose()

    app = FastAPI(title="Notify Hub", version="0.4.7", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.clock = clock
    app.state.login_limiter = SlidingWindowLimiter(clock)
    app.state.api_limiter = SlidingWindowLimiter(clock)
    app.state.event_service = event_service
    app.state.notification_service = NotificationService(factory, clock)
    app.state.notification_channel = channel
    app.state.delivery_worker = worker
    app.state.reminder_service = reminder_service
    app.state.conversation_service = conversation_service
    app.state.wecom_callback_service = callback_service
    app.state.media_service = media_service
    app.state.tts_media_service = tts_media_service
    app.state.plugin_service = plugin_service
    app.state.plugin_worker = plugin_worker
    app.state.secret_store = secret_store
    if (
        settings.wecom_corp_id
        and settings.wecom_callback_token is not None
        and settings.wecom_callback_aes_key is not None
    ):
        app.state.wecom_callback_crypto = WeComCrypto(
            token=settings.wecom_callback_token.get_secret_value(),
            encoding_aes_key=settings.wecom_callback_aes_key.get_secret_value(),
            corp_id=settings.wecom_corp_id,
            replay_window_seconds=settings.wecom_callback_replay_window_seconds,
        )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or new_request_id()
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    app.include_router(health_router)

    # Routers are imported lazily so infrastructure modules remain independently testable.
    from app.api.admin_auth import router as auth_router
    from app.api.admin_core import router as admin_router
    from app.api.admin_management import router as management_router
    from app.api.events import router as events_router
    from app.api.media import router as media_router
    from app.api.plugins import router as plugins_router
    from app.api.reminders import router as reminders_router
    from app.api.wecom_callback import router as callback_router

    app.include_router(auth_router, prefix="/api/v1/admin/auth")
    app.include_router(admin_router, prefix="/api/v1/admin")
    app.include_router(management_router, prefix="/api/v1/admin")
    app.include_router(events_router, prefix="/api/v1")
    app.include_router(plugins_router, prefix="/api/v1/admin")
    app.include_router(reminders_router, prefix="/api/v1/admin")
    app.include_router(callback_router, prefix="/api/v1")
    app.include_router(media_router)
    install_error_handlers(app)
    from pathlib import Path

    static_dir = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if static_dir.is_dir():
        app.mount("/", SPAStaticFiles(directory=static_dir, html=True), name="frontend")
    return app


app = create_app()
