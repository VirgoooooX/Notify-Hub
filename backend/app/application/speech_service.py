from __future__ import annotations

import enum
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.media.errors import MediaError


class SpeechError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class ASRAdapter(Protocol):
    async def transcribe(self, audio: bytes, *, mime_type: str) -> str: ...


class TTSAdapter(Protocol):
    async def synthesize(self, text: str) -> bytes: ...


class AudioTranscoder(Protocol):
    async def to_wecom_amr(self, audio: bytes) -> bytes: ...


@dataclass(frozen=True, slots=True)
class RecognitionDraft:
    text: str
    source: str


class SpeechRecognitionService:
    """Produces draft text only; it has no dependency on ReminderService."""

    def __init__(self, asr: ASRAdapter) -> None:
        self._asr = asr

    async def recognize(
        self,
        recognition: str | None,
        *,
        download_voice: Callable[[], Awaitable[bytes]],
        mime_type: str = "audio/amr",
    ) -> RecognitionDraft:
        provided = (recognition or "").strip()
        if provided:
            return RecognitionDraft(provided, "wecom_recognition")
        try:
            audio = await download_voice()
            text = (await self._asr.transcribe(audio, mime_type=mime_type)).strip()
        except (MediaError, SpeechError):
            raise
        except Exception as exc:
            raise SpeechError("asr_failed", "Speech recognition failed", retryable=True) from exc
        if not text:
            raise SpeechError("asr_empty", "Speech recognition returned no text")
        return RecognitionDraft(text, "asr")


@dataclass(frozen=True, slots=True)
class VoicePermission:
    allow_voice: bool
    allow_high_priority: bool = False
    allow_critical: bool = False


def authorize_voice_request(priority: str, permission: VoicePermission) -> None:
    if not permission.allow_voice:
        raise SpeechError("voice_forbidden", "Actor is not allowed to request voice delivery")
    if priority not in {"high", "critical"}:
        raise SpeechError(
            "voice_priority_required", "Voice delivery requires high or critical priority"
        )
    if priority == "high" and not permission.allow_high_priority:
        raise SpeechError("priority_forbidden", "Actor is not allowed to use high priority")
    if priority == "critical" and not permission.allow_critical:
        raise SpeechError("priority_forbidden", "Actor is not allowed to use critical priority")


class VoiceSendState(str, enum.Enum):
    SUCCEEDED = "succeeded"
    CONFIRMED_FAILED = "confirmed_failed"
    UNKNOWN = "unknown"


class VoiceNotSentError(SpeechError):
    """A pre-send failure for which text fallback is known to be safe."""

    def __init__(self, message: str = "Voice was not sent") -> None:
        super().__init__("voice_not_sent", message, retryable=True)


class TextFallbackStore(Protocol):
    async def create_once(self, source_delivery_id: str, text: str) -> bool: ...


class VoiceDeliveryService:
    """Coordinates voice generation and the exactly-once text fallback decision."""

    def __init__(
        self,
        tts: TTSAdapter,
        transcoder: AudioTranscoder,
        fallback_store: TextFallbackStore,
    ) -> None:
        self._tts = tts
        self._transcoder = transcoder
        self._fallback_store = fallback_store

    async def deliver(
        self,
        *,
        delivery_id: str,
        text: str,
        upload_and_send: Callable[[bytes], Awaitable[VoiceSendState]],
    ) -> VoiceSendState:
        try:
            speech = await self._tts.synthesize(text)
            voice = await self._transcoder.to_wecom_amr(speech)
        except Exception:
            # Voice was not sent yet, so a fallback cannot create a duplicate.
            await self._fallback_store.create_once(delivery_id, text)
            return VoiceSendState.CONFIRMED_FAILED

        try:
            state = await upload_and_send(voice)
        except VoiceNotSentError:
            # Upload failed before the voice send request was issued.
            await self._fallback_store.create_once(delivery_id, text)
            return VoiceSendState.CONFIRMED_FAILED
        except Exception:
            # An exception during upload/send may mean the provider accepted the
            # message but the response was lost. Never double-send on ambiguity.
            return VoiceSendState.UNKNOWN
        if state is VoiceSendState.CONFIRMED_FAILED:
            await self._fallback_store.create_once(delivery_id, text)
        return state
