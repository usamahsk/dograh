"""Genesys Cloud CX AudioHook telephony configuration schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class GenesysConfigurationRequest(BaseModel):
    """Request schema for Genesys Cloud CX AudioHook configuration.

    Unlike the other providers these credentials are chosen by the Dograh
    user (not issued by Genesys): the API key and client secret are entered
    on both sides — here and in the Genesys Audio Connector integration
    credentials tab. Genesys sends the API key in the ``X-API-KEY`` header of
    the WebSocket upgrade and signs the request with the client secret.
    """

    provider: Literal["genesys"] = Field(default="genesys")
    api_key: str = Field(
        ...,
        min_length=8,
        description="API key Dograh expects in the X-API-KEY header (paste the same value into the Genesys Audio Connector credentials)",
    )
    client_secret: str = Field(
        default="",
        description="Client secret used to verify the RFC 9421 request signature. Leave empty to accept unsigned connections.",
    )
    genesys_client_id: str = Field(
        default="",
        description="Genesys Cloud OAuth client ID — enables live in-call attribute updates (per-turn logging to the conversation)",
    )
    genesys_client_secret: str = Field(
        default="",
        description="Genesys Cloud OAuth client secret",
    )
    genesys_region: str = Field(
        default="",
        description="Genesys Cloud region host, e.g. usw2.pure.cloud",
    )
    default_workflow_uuid: str = Field(
        default="",
        description="Fallback agent (workflow UUID) for this configuration. Used when the Connector ID Genesys appends is not a known Dograh agent — lets you switch the agent from Dograh without editing the Genesys Architect flow.",
    )


class GenesysConfigurationResponse(BaseModel):
    """Response schema for Genesys configuration with masked sensitive fields."""

    provider: Literal["genesys"] = Field(default="genesys")
    api_key: str  # Masked
    client_secret: str  # Masked
    genesys_client_id: str = ""
    genesys_client_secret: str = ""  # Masked
    genesys_region: str = ""
    default_workflow_uuid: str = ""
