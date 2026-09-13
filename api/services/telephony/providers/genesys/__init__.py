"""Genesys Cloud CX AudioHook telephony provider package."""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import GenesysConfigurationRequest, GenesysConfigurationResponse
from .provider import GenesysProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "genesys",
        "api_key": value.get("api_key"),
        "client_secret": value.get("client_secret", ""),
        "genesys_client_id": value.get("genesys_client_id", ""),
        "genesys_client_secret": value.get("genesys_client_secret", ""),
        "genesys_region": value.get("genesys_region", ""),
        "default_workflow_uuid": value.get("default_workflow_uuid", ""),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="Genesys Cloud CX (AudioHook)",
    docs_url="https://developer.genesys.cloud/devapps/audiohook/",
    fields=[
        ProviderUIField(
            name="api_key",
            label="API Key",
            type="password",
            sensitive=True,
            description="Value Genesys sends in the X-API-KEY header. Enter the same value in the Genesys Audio Connector integration credentials.",
        ),
        ProviderUIField(
            name="client_secret",
            label="Client Secret",
            type="password",
            sensitive=True,
            required=False,
            description="Used to verify the signed WebSocket upgrade from Genesys. Enter the same value in the Genesys Audio Connector credentials. Leave empty to accept unsigned connections.",
        ),
        ProviderUIField(
            name="genesys_client_id",
            label="Genesys API Client ID",
            type="text",
            required=False,
            description="Genesys Cloud OAuth client ID. Enables live per-turn call logging to the conversation (participant attributes).",
        ),
        ProviderUIField(
            name="genesys_client_secret",
            label="Genesys API Client Secret",
            type="password",
            sensitive=True,
            required=False,
            description="Genesys Cloud OAuth client secret for the client ID above.",
        ),
        ProviderUIField(
            name="genesys_region",
            label="Genesys Region",
            type="text",
            required=False,
            placeholder="usw2.pure.cloud",
            description="Genesys Cloud region host (e.g. usw2.pure.cloud, eu-central-1.mypurecloud.de).",
        ),
        ProviderUIField(
            name="default_workflow_uuid",
            label="Default Agent (Workflow UUID)",
            type="text",
            required=False,
            description="Agent used when the Connector ID in the Genesys Architect action is not a Dograh agent UUID. Lets you switch the agent per configuration (e.g. separate inbound/outbound configs) without editing the Genesys flow.",
        ),
    ],
)


SPEC = ProviderSpec(
    name="genesys",
    provider_cls=GenesysProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=GenesysConfigurationRequest,
    config_response_cls=GenesysConfigurationResponse,
    ui_metadata=_UI_METADATA,
    account_id_credential_field="api_key",
)


register(SPEC)


__all__ = [
    "SPEC",
    "GenesysConfigurationRequest",
    "GenesysConfigurationResponse",
    "GenesysProvider",
    "create_transport",
]
