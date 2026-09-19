"""Genesys Cloud CX AudioHook transport factory."""

from fastapi import WebSocket
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from api.services.pipecat.transport_params import realtime_param_overrides
from api.services.telephony.factory import load_credentials_for_transport

from .genesys_api import GenesysApiClient
from .serializers import DograhGenesysAudioHookSerializer
from .session_logger import GenesysSessionLogger


async def create_transport(
    websocket: WebSocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    ambient_noise_config: dict | None = None,
    telephony_configuration_id: int | None = None,
    is_realtime: bool = False,
):
    """Create a transport for a Genesys AudioHook connection.

    Audio is PCMU μ-law at 8 kHz over the AudioHook protocol; the serializer
    resamples to/from the pipeline rate. ``fixed_audio_packet_size`` keeps
    outbound audio in 1600-byte packets — smaller (or unbatched) packets get
    rate-limited (HTTP 429) by Genesys Cloud.
    """
    # Validate that the run's telephony configuration exists and is a Genesys
    # config (the serializer itself needs no credentials).
    config = await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="genesys"
    )

    # Live conversation-attribute logging (Genesys REST API OAuth flow).
    # Re-enabled per the documented approach: valid OAuth client credentials
    # on the configuration drive per-turn attribute pushes (see session_logger).
    session_logger = None
    client_id = config.get("genesys_client_id")
    client_secret = config.get("genesys_client_secret")
    region = config.get("genesys_region")
    if client_id and client_secret and region:
        api_client = GenesysApiClient(client_id, client_secret, region)
        session_logger = GenesysSessionLogger(
            api_client=api_client,
            workflow_run_id=workflow_run_id,
        )

    serializer = DograhGenesysAudioHookSerializer(
        params=DograhGenesysAudioHookSerializer.InputParams(
            genesys_sample_rate=8000,
            sample_rate=audio_config.pipeline_sample_rate,
        ),
        session_logger=session_logger,
    )

    mixer = await build_audio_out_mixer(
        audio_config.transport_out_sample_rate, ambient_noise_config
    )

    return FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            audio_out_mixer=mixer,
            fixed_audio_packet_size=1600,
            serializer=serializer,
            **realtime_param_overrides(is_realtime),
        ),
    )
