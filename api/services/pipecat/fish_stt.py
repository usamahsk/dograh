"""Fish Audio speech-to-text service (HTTP batch API).

Implements Pipecat's ``SegmentedSTTService`` contract against Fish Audio's
documented Speech-to-Text endpoint (``POST /v1/asr``, see
https://docs.fish.audio/api-reference/introduction and the canonical
OpenAPI schema at https://api.fish.audio/openapi.json), following the same
structure as Pipecat's own HTTP STT services (e.g. Fal Wizper):

- The base class buffers VAD-delimited speech and hands each segment to
  ``run_stt`` as a WAV container — exactly what ``/v1/asr`` accepts
  ("send the raw file bytes; no pre-processing required").
- ``run_stt`` POSTs the WAV bytes as MessagePack
  (``{audio, language, ignore_timestamps}``) with ``Authorization: Bearer``
  and maps ``{text, duration, segments}`` onto a ``TranscriptionFrame``.
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass

import aiohttp
import msgpack
from loguru import logger

from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transcriptions.language import Language, resolve_language
from pipecat.utils.time import time_now_iso8601
from pipecat.utils.tracing.service_decorators import traced_stt

FISH_AUDIO_ASR_URL = "https://api.fish.audio/v1/asr"


def language_to_fish_audio_stt_language(language: Language) -> str:
    """Convert a Language enum to a Fish Audio ASR language code.

    Args:
        language: The Language enum value to convert.

    Returns:
        The Fish Audio language code (ISO 639-1 in practice).
    """
    LANGUAGE_MAP = {
        Language.ZH: "zh",
        Language.EN: "en",
        Language.HI: "hi",
        Language.ES: "es",
        Language.FR: "fr",
        Language.DE: "de",
        Language.JA: "ja",
        Language.PT: "pt",
        Language.AR: "ar",
        Language.RU: "ru",
        Language.IT: "it",
        Language.NL: "nl",
    }
    return resolve_language(language, LANGUAGE_MAP, use_base_code=True)


@dataclass
class FishAudioSTTSettings(STTSettings):
    """Settings for FishAudioSTTService."""

    pass


class FishAudioSTTService(SegmentedSTTService):
    """Speech-to-text service using Fish Audio's HTTP ``/v1/asr`` API.

    Transcribes each VAD-delimited audio segment with a single HTTP call.
    Latency is higher than a realtime WebSocket STT; this is the documented
    Fish Audio transcription endpoint for API keys.

    Example::

        stt = FishAudioSTTService(
            api_key="your-api-key",
            settings=FishAudioSTTService.Settings(
                language=Language.EN,
            ),
        )
    """

    Settings = FishAudioSTTSettings
    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = FISH_AUDIO_ASR_URL,
        aiohttp_session: aiohttp.ClientSession | None = None,
        ignore_timestamps: bool = True,
        sample_rate: int | None = None,
        settings: Settings | None = None,
        **kwargs,
    ):
        """Initialize the Fish Audio STT service.

        Args:
            api_key: Fish Audio API key for authentication.
            base_url: Fish Audio ASR endpoint URL. Defaults to the
                production ``POST /v1/asr`` endpoint.
            aiohttp_session: Optional aiohttp ClientSession for HTTP requests.
                If not provided, a session is created and managed internally.
            ignore_timestamps: Skip precise word timestamps for lower latency
                on short audio (Fish Audio default). Passed as
                ``ignore_timestamps`` in the request.
            sample_rate: Audio sample rate in Hz. If None, uses the pipeline's rate.
            settings: Runtime-updatable settings for the STT service.
            **kwargs: Additional arguments passed to SegmentedSTTService.
        """
        default_settings = self.Settings(
            model="fish-asr",
            language=Language.EN,
        )

        if settings is not None:
            default_settings.apply_update(settings)

        super().__init__(
            sample_rate=sample_rate,
            settings=default_settings,
            **kwargs,
        )

        self._api_key = api_key
        if not self._api_key:
            raise ValueError("Fish Audio API key must be provided via api_key")
        self._base_url = base_url.rstrip("/")
        self._ignore_timestamps = ignore_timestamps
        self._session: aiohttp.ClientSession | None = aiohttp_session

    def can_generate_metrics(self) -> bool:
        """Check if this service can generate processing metrics."""
        return True

    def language_to_service_language(self, language: Language) -> str | None:
        """Convert a Language enum to Fish Audio's service language code.

        Args:
            language: The language to convert.

        Returns:
            The Fish Audio language code, or None if not supported.
        """
        return language_to_fish_audio_stt_language(language)

    def _request_language(self) -> str | None:
        """Language for the ``/v1/asr`` request (None = auto-detect)."""
        language = self._settings.language
        if language is None:
            return None
        if isinstance(language, Language):
            return self.language_to_service_language(language)
        if isinstance(language, str):
            if not language or language == "auto":
                return None
            return language
        return None

    @traced_stt
    async def _handle_transcription(
        self, transcript: str, is_final: bool, language: str | None = None
    ):
        """Handle a transcription result with tracing."""
        await self.stop_processing_metrics()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Transcribe one WAV audio segment with Fish Audio's ``/v1/asr`` API.

        Args:
            audio: Audio segment bytes in WAV format (already converted by
                the base class).

        Yields:
            Frame: TranscriptionFrame with the transcribed text, or ErrorFrame
                on failure. Only non-empty transcriptions are yielded.
        """
        try:
            await self.start_processing_metrics()

            if not self._session:
                self._session = aiohttp.ClientSession()

            payload = {
                "audio": audio,
                "language": self._request_language(),
                "ignore_timestamps": self._ignore_timestamps,
            }
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/msgpack",
            }

            async with self._session.post(
                self._base_url,
                data=msgpack.packb(payload, use_bin_type=True),
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    yield ErrorFrame(
                        error=f"Fish Audio API error ({resp.status}): {error_text}"
                    )
                    return
                response = await resp.json()

            if response and response.get("text"):
                text = response["text"].strip()
                if text:  # Only yield non-empty text
                    language = self._request_language()
                    await self._handle_transcription(text, True, language)
                    logger.debug(f"Fish Audio transcription: [{text}]")
                    yield TranscriptionFrame(
                        text,
                        self._user_id,
                        time_now_iso8601(),
                        language,
                        result=response,
                    )

        except Exception as e:
            yield ErrorFrame(error=f"Unknown error occurred: {e}")
