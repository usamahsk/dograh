"""Conversation behavior shared by Dograh's realtime service adapters."""

import time
from array import array
from dataclasses import replace

from loguru import logger

from pipecat.audio.resamplers.base_audio_resampler import BaseAudioResampler
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    TTSSpeakFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection

# How often (seconds) the Gemini inlet probe summarizes what it sees. Frequent
# enough to catch a stuck mute or dead inlet within one test call, sparse
# enough to stay out of the way.
_INLET_PROBE_INTERVAL_SECS = 5.0


def _frame_peak(audio: bytes) -> int:
    """Peak absolute PCM16 sample in a frame (0 for empty/silent).

    Uses :mod:`array` (not :mod:`audioop`, removed in Python 3.13) so this
    runs on every supported runtime.
    """
    if not audio or len(audio) < 2:
        return 0
    try:
        samples = array("h", audio[: len(audio) - (len(audio) % 2)])
        peak = 0
        for s in samples:
            a = -s - 1 if s < 0 else s
            if a > peak:
                peak = a
        return peak
    except Exception:
        return 1 if any(audio) else 0


class RealtimeConversationMixin:
    """Share greeting and mute policy without owning provider session state.

    Providers implement ``_handle_initial_greeting`` and ``_handle_context``;
    both mark ``_handled_initial_context`` once they accept the opening turn.
    That flag belongs to the conversation and survives node reconnects.
    Session readiness, turn detection, and reconnects remain upstream concerns.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._user_is_muted = False
        # The engine assigns _context before the first context frame arrives.
        self._handled_initial_context = False
        # Inlet probe: counts InputAudioRawFrames arriving at the realtime
        # service (post-aggregator, pre-provider). Diagnoses "caller audio
        # never becomes a user turn" as dead inlet vs stuck mute vs silence.
        self._inlet_frames = 0
        self._inlet_muted_frames = 0
        self._inlet_silent_frames = 0
        self._inlet_peak = 0
        self._inlet_last_log = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, (UserMuteStartedFrame, UserMuteStoppedFrame)):
            muted = isinstance(frame, UserMuteStartedFrame)
            if muted != self._user_is_muted:
                logger.info(f"{self}: caller mute {'STARTED' if muted else 'STOPPED'}")
            self._user_is_muted = muted
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, TTSSpeakFrame):
            # Realtime owns speech output. Only the opening TTS trigger is
            # meaningful; later prompts use LLMMessagesAppendFrame instead.
            if self._handled_initial_context:
                logger.debug(f"{self}: ignoring text speech after the opening turn")
                return
            text = frame.text.strip() if frame.text else ""
            if text:
                await self._handle_initial_greeting(self._context, text)
            else:
                await self._handle_context(self._context)
            return
        await super().process_frame(frame, direction)

    async def _send_user_audio(self, frame: InputAudioRawFrame):
        await super()._send_user_audio(await self._prepare_user_audio(frame))

    async def _prepare_user_audio(self, frame: InputAudioRawFrame):
        return await self._prepare_audio_frame(frame)

    async def _prepare_audio_frame(
        self,
        frame: InputAudioRawFrame,
        *,
        sample_rate: int | None = None,
        resampler: BaseAudioResampler | None = None,
        silence_byte: int = 0,
    ) -> InputAudioRawFrame:
        """Keep the input clock running with silence while the caller is muted.

        Mask before resampling so muted speech cannot enter its history, and
        after resampling to remove any previously buffered caller audio. Copy
        frames so recording and other consumers retain the original audio.
        """
        muted = self._user_is_muted
        audio = bytes([silence_byte]) * len(frame.audio) if muted else frame.audio
        target_rate = sample_rate or frame.sample_rate
        if resampler is not None and frame.sample_rate != target_rate:
            audio = await resampler.resample(audio, frame.sample_rate, target_rate)
        if muted or self._user_is_muted:
            audio = bytes([silence_byte]) * len(audio)
        self._probe_inlet(frame, muted=muted)
        if audio is frame.audio and target_rate == frame.sample_rate:
            return frame
        return replace(frame, audio=audio, sample_rate=target_rate)

    def _probe_inlet(self, frame: InputAudioRawFrame, *, muted: bool) -> None:
        """Summarize caller audio reaching the realtime service (throttled).

        One line every few seconds tells the whole story:
        - frames == 0      -> inlet dead (transport/deserializer, pre-aggregator)
        - muted == frames  -> mute stuck ON (Gemini fed silence by design)
        - silent == frames -> inbound media is digital silence (wrong stream?)
        - peak > 0 unmuted -> audio reaches Gemini; look at provider
          transcription next, not the transport.
        """
        self._inlet_frames += 1
        if muted:
            self._inlet_muted_frames += 1
        else:
            peak = _frame_peak(frame.audio)
            if peak > self._inlet_peak:
                self._inlet_peak = peak
            if peak == 0:
                self._inlet_silent_frames += 1
        now = time.monotonic()
        if now - self._inlet_last_log >= _INLET_PROBE_INTERVAL_SECS:
            self._inlet_last_log = now
            logger.info(
                f"{self}: realtime-inlet frames={self._inlet_frames} "
                f"muted={self._inlet_muted_frames} "
                f"silent_unmuted={self._inlet_silent_frames} "
                f"peak={self._inlet_peak} rate={frame.sample_rate}"
            )
