from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

from app.application.speech_service import SpeechError
from app.media.validation import MediaKind, validate_media


class _LocalCommand:
    def __init__(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float = 30.0,
        output_limit_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        if not argv or not argv[0].strip() or any("\x00" in item for item in argv):
            raise ValueError("A fixed, valid command argv is required")
        self._argv = tuple(argv)
        self._timeout = timeout_seconds
        self._output_limit = output_limit_bytes

    def _render(self, input_path: Path, output_path: Path) -> tuple[str, ...]:
        return tuple(
            item.replace("{input}", str(input_path)).replace("{output}", str(output_path))
            for item in self._argv
        )

    async def run(
        self, data: bytes, *, input_suffix: str, output_suffix: str
    ) -> tuple[bytes, bytes]:
        temporary = Path(tempfile.mkdtemp(prefix="notify-hub-speech-"))
        input_path = temporary / f"input{input_suffix}"
        output_path = temporary / f"output{output_suffix}"
        try:
            await asyncio.to_thread(input_path.write_bytes, data)
            process = await asyncio.create_subprocess_exec(
                *self._render(input_path, output_path),
                cwd=temporary,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert process.stdout is not None
            assert process.stderr is not None
            stdout_stream = process.stdout
            stderr_stream = process.stderr

            async def collect() -> tuple[bytes, bytes, int]:
                stdout_task = asyncio.create_task(stdout_stream.read(self._output_limit + 1))
                stderr_task = asyncio.create_task(stderr_stream.read(self._output_limit + 1))
                stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
                if len(stdout) > self._output_limit or len(stderr) > self._output_limit:
                    process.kill()
                    await process.wait()
                    raise SpeechError(
                        "speech_output_too_large", "Speech command output limit exceeded"
                    )
                return stdout, stderr, await process.wait()

            try:
                stdout, _stderr, return_code = await asyncio.wait_for(
                    collect(), timeout=self._timeout
                )
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise SpeechError(
                    "speech_timeout", "Speech command timed out", retryable=True
                ) from exc
            if return_code != 0:
                raise SpeechError("speech_command_failed", "Speech command failed")
            output = b""
            if output_path.exists():
                size = output_path.stat().st_size
                if size > self._output_limit:
                    raise SpeechError("speech_output_too_large", "Speech output limit exceeded")
                output = await asyncio.to_thread(output_path.read_bytes)
            return stdout, output
        finally:
            await asyncio.to_thread(shutil.rmtree, temporary, True)


class LocalCommandASR:
    def __init__(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float = 60.0,
        output_limit_bytes: int = 64 * 1024,
    ) -> None:
        self._command = _LocalCommand(
            argv, timeout_seconds=timeout_seconds, output_limit_bytes=output_limit_bytes
        )

    async def transcribe(self, audio: bytes, *, mime_type: str) -> str:
        suffix = ".amr" if mime_type == "audio/amr" else ".audio"
        stdout, output = await self._command.run(audio, input_suffix=suffix, output_suffix=".txt")
        raw = output or stdout
        try:
            return raw.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise SpeechError("asr_invalid_output", "ASR output is not UTF-8") from exc


class LocalCommandTTS:
    def __init__(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float = 60.0,
        output_limit_bytes: int = 2 * 1024 * 1024,
        output_suffix: str = ".wav",
    ) -> None:
        if not any("{output}" in item for item in argv):
            raise ValueError("TTS argv must include an {output} placeholder")
        self._command = _LocalCommand(
            argv, timeout_seconds=timeout_seconds, output_limit_bytes=output_limit_bytes
        )
        self._output_suffix = output_suffix

    async def synthesize(self, text: str) -> bytes:
        if not text.strip():
            raise SpeechError("tts_empty_text", "TTS text is empty")
        _stdout, output = await self._command.run(
            text.encode("utf-8"), input_suffix=".txt", output_suffix=self._output_suffix
        )
        if not output:
            raise SpeechError("tts_empty_output", "TTS produced no audio")
        return output


class LocalCommandAmrTranscoder:
    def __init__(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float = 30.0,
        output_limit_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        if not any("{output}" in item for item in argv):
            raise ValueError("Transcoder argv must include an {output} placeholder")
        self._command = _LocalCommand(
            argv, timeout_seconds=timeout_seconds, output_limit_bytes=output_limit_bytes
        )

    async def to_wecom_amr(self, audio: bytes) -> bytes:
        _stdout, output = await self._command.run(
            audio, input_suffix=".audio", output_suffix=".amr"
        )
        validate_media(output, MediaKind.VOICE)
        return output
