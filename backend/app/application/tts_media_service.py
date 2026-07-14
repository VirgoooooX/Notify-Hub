from __future__ import annotations

from app.application.media_service import MediaService
from app.application.speech_service import AudioTranscoder, TTSAdapter
from app.infrastructure.database.media_models import MediaAsset
from app.media.validation import MediaKind
from sqlalchemy.ext.asyncio import AsyncSession


class TtsMediaService:
    def __init__(
        self,
        tts: TTSAdapter,
        transcoder: AudioTranscoder,
        media: MediaService,
    ) -> None:
        self._tts = tts
        self._transcoder = transcoder
        self._media = media

    async def create_voice(
        self, session: AsyncSession, text: str, *, created_by: str
    ) -> MediaAsset:
        audio = await self._tts.synthesize(text)
        amr = await self._transcoder.to_wecom_amr(audio)
        return await self._media.create(
            session,
            amr,
            MediaKind.VOICE,
            source="tts",
            created_by=created_by,
        )
