"""Genesys AudioHook serializers.

Re-exported from the vendored pipecat, plus a Dograh subclass that carries
session outputs (disposition, extracted variables, end reason) back to the
Genesys Architect flow in the disconnect message's ``outputVariables``.
"""

import asyncio
import json
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.serializers.genesys import (  # noqa: F401
    AudioHookChannel,
    AudioHookMediaFormat,
    GenesysAudioHookSerializer,
)

__all__ = [
    "DograhGenesysAudioHookSerializer",
    "GenesysAudioHookSerializer",
    "AudioHookChannel",
    "AudioHookMediaFormat",
]

# Marker key on an OutputTransportMessageUrgentFrame: the engine queues this
# just before EndFrame when the serializer opts in via supports_session_outputs.
SESSION_OUTPUTS_MARKER = "dograh_session_outputs"

# Reasons that mean "conversation over, nothing to hand off" (everything else
# reads as "hand control back to the flow for routing/transfer").
_FINISHED_REASONS = {
    "user_hangup",
    "voicemail_detected",
    "call_duration_exceeded",
    "user_idle_max_duration_exceeded",
    "system_connect_error",
    "pipeline_error",
}


class DograhGenesysAudioHookSerializer(GenesysAudioHookSerializer):
    """Genesys AudioHook serializer with Architect output-variable support.

    The engine queues an ``OutputTransportMessageUrgentFrame`` carrying
    ``{SESSION_OUTPUTS_MARKER: {...}}`` right before EndFrame when this
    serializer is in the transport. We capture it (without emitting anything
    on the wire) and include the variables in the ``disconnect`` message's
    ``outputVariables`` so the Architect flow can branch and transfer.

    When a ``session_logger`` is attached (Genesys API credentials configured),
    per-turn activity is also mirrored live onto the conversation attributes.
    """

    supports_session_outputs = True

    def __init__(self, *args, session_logger=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_logger = session_logger

    def create_logs_listener(self):
        """Factory used by the pipeline to attach the feedback-event listener."""
        if self.session_logger is None:
            return None

        async def listener(event: dict) -> None:
            await self.session_logger.handle_event(event)

        return listener

    async def _handle_open(self, message: dict[str, Any]) -> Frame | None:
        """Log the Architect input variables (e.g. InputCallerID, InputCallerName)."""
        frame = await super()._handle_open(message)
        inputs = self._input_variables
        if inputs:
            logger.info(f"Genesys input variables from Architect: {inputs}")
        if self.session_logger is not None:
            self.session_logger.set_conversation_id(self._conversation_id)
            self.session_logger.set_session_id(self._session_id)
        return frame

    async def _handle_close(self, message: dict[str, Any]) -> Frame | None:
        frame = await super()._handle_close(message)
        if self.session_logger is not None:
            asyncio.get_running_loop().create_task(self.session_logger.finalize())
        return frame

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, OutputTransportMessageUrgentFrame):
            message = frame.message
            if (
                isinstance(message, dict)
                and SESSION_OUTPUTS_MARKER in message
                and isinstance(message[SESSION_OUTPUTS_MARKER], dict)
            ):
                outputs = self._sanitize(message[SESSION_OUTPUTS_MARKER])
                self.set_output_variables(outputs)
                logger.info(f"Genesys session outputs captured: {outputs}")
                return None
            # Any other passthrough message: defer to the base class.

        if isinstance(frame, (EndFrame, CancelFrame)):
            outputs = dict(self.output_variables or {})
            reason = getattr(frame, "reason", "") or ""
            if reason:
                outputs.setdefault("endReason", reason)
            if outputs:
                self.set_output_variables(outputs)

        return await super().serialize(frame)

    @staticmethod
    def _sanitize(outputs: dict[str, Any]) -> dict[str, Any]:
        """AudioHook outputVariables must be flat scalars."""
        clean: dict[str, Any] = {}
        for key, value in outputs.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                clean[key] = value
            elif isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if isinstance(sub_value, (str, int, float, bool)) or sub_value is None:
                        clean.setdefault(str(sub_key), sub_value)
        return clean

