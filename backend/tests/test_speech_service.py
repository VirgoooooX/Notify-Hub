from __future__ import annotations

import pytest
from app.application.speech_service import (
    RecognitionDraft,
    SpeechError,
    SpeechRecognitionService,
    VoiceDeliveryService,
    VoiceNotSentError,
    VoiceSendState,
)


class StubASR:
    def __init__(self, result: str = "ASR draft", *, fail: bool = False) -> None:
        self.calls = 0
        self.result = result
        self.fail = fail

    async def transcribe(self, _audio: bytes, *, mime_type: str) -> str:
        assert mime_type == "audio/amr"
        self.calls += 1
        if self.fail:
            raise SpeechError("asr_failed", "failed")
        return self.result


@pytest.mark.asyncio
async def test_wecom_recognition_is_preferred_without_media_download() -> None:
    asr = StubASR()
    downloaded = False

    async def download() -> bytes:
        nonlocal downloaded
        downloaded = True
        return b"audio"

    result = await SpeechRecognitionService(asr).recognize(
        " built-in draft ", download_voice=download
    )
    assert result == RecognitionDraft("built-in draft", "wecom_recognition")
    assert asr.calls == 0
    assert downloaded is False


@pytest.mark.asyncio
async def test_asr_failure_produces_no_draft() -> None:
    service = SpeechRecognitionService(StubASR(fail=True))
    with pytest.raises(SpeechError, match="failed"):
        await service.recognize(None, download_voice=lambda: _value(b"audio"))


async def _value(value: bytes) -> bytes:
    return value


class PassTTS:
    async def synthesize(self, text: str) -> bytes:
        return text.encode()


class PassTranscoder:
    async def to_wecom_amr(self, audio: bytes) -> bytes:
        return audio


class OnceFallback:
    def __init__(self) -> None:
        self.created: set[str] = set()

    async def create_once(self, source_delivery_id: str, _text: str) -> bool:
        before = len(self.created)
        self.created.add(source_delivery_id)
        return len(self.created) != before


@pytest.mark.asyncio
async def test_confirmed_upload_failure_creates_only_one_text_fallback() -> None:
    fallback = OnceFallback()
    service = VoiceDeliveryService(PassTTS(), PassTranscoder(), fallback)

    async def fail_upload(_voice: bytes) -> VoiceSendState:
        raise VoiceNotSentError("upload failed")

    await service.deliver(delivery_id="delivery-1", text="hello", upload_and_send=fail_upload)
    await service.deliver(delivery_id="delivery-1", text="hello", upload_and_send=fail_upload)
    assert fallback.created == {"delivery-1"}


@pytest.mark.asyncio
async def test_uncertain_voice_send_never_creates_text_fallback() -> None:
    fallback = OnceFallback()
    service = VoiceDeliveryService(PassTTS(), PassTranscoder(), fallback)

    async def uncertain(_voice: bytes) -> VoiceSendState:
        raise TimeoutError

    result = await service.deliver(
        delivery_id="delivery-1", text="hello", upload_and_send=uncertain
    )
    assert result is VoiceSendState.UNKNOWN
    assert fallback.created == set()
