"""Tests for Genesys provider config schemas and registry registration."""

import pytest
from pydantic import TypeAdapter, ValidationError

from api.services.telephony import registry
from api.services.telephony.providers.genesys.provider import GenesysProvider
from api.services.telephony.providers.genesys.config import (
    GenesysConfigurationRequest,
    GenesysConfigurationResponse,
)


def test_genesys_provider_is_registered():
    spec = registry.get_optional("genesys")
    assert spec is not None
    assert spec.provider_cls is GenesysProvider
    assert spec.transport_sample_rate == 8000
    assert spec.account_id_credential_field == "api_key"


def test_config_request_union_dispatches_on_provider_literal():
    from api.schemas.telephony_config import TelephonyConfigRequest

    # TelephonyConfigRequest is a Union — validate via TypeAdapter.
    request = TypeAdapter(TelephonyConfigRequest).validate_python(
        {"provider": "genesys", "api_key": "test-api-key-123", "client_secret": "s3cret"}
    )
    assert isinstance(request, GenesysConfigurationRequest)
    assert request.api_key == "test-api-key-123"
    assert request.client_secret == "s3cret"


def test_config_request_client_secret_optional():
    request = GenesysConfigurationRequest(api_key="test-api-key-123")
    assert request.client_secret == ""


def test_config_request_requires_api_key():
    with pytest.raises(ValidationError):
        GenesysConfigurationRequest()


def test_config_loader_reshapes_credentials():
    spec = registry.get("genesys")
    loaded = spec.config_loader(
        {"api_key": "k", "client_secret": "s", "unknown_field": "x"}
    )
    # Loader preserves credentials and defaults the Genesys-specific
    # fields (client id/secret, region, agent workflow uuid).
    assert loaded == {
        "provider": "genesys",
        "api_key": "k",
        "client_secret": "s",
        "genesys_client_id": "",
        "genesys_client_secret": "",
        "genesys_region": "",
        "default_workflow_uuid": "",
    }


def test_config_loader_preserves_genesys_fields():
    spec = registry.get("genesys")
    loaded = spec.config_loader(
        {
            "api_key": "k",
            "genesys_client_id": "cid",
            "genesys_client_secret": "csecret",
            "genesys_region": "us-east-1",
            "default_workflow_uuid": "6f1c2e34-uuid",
        }
    )
    assert loaded["genesys_client_id"] == "cid"
    assert loaded["genesys_client_secret"] == "csecret"
    assert loaded["genesys_region"] == "us-east-1"
    assert loaded["default_workflow_uuid"] == "6f1c2e34-uuid"


def test_ui_metadata_marks_credentials_sensitive():
    spec = registry.get("genesys")
    sensitive = {f.name for f in spec.ui_metadata.fields if f.sensitive}
    assert sensitive == {"api_key", "client_secret", "genesys_client_secret"}


def test_validate_account_id_matches_api_key():
    assert GenesysProvider.validate_account_id({"api_key": "k1"}, "k1")
    assert not GenesysProvider.validate_account_id({"api_key": "k1"}, "k2")


def test_outbound_initiation_not_supported():
    provider = GenesysProvider({"api_key": "k"})
    assert not provider.supports_transfers()
    with pytest.raises(NotImplementedError):
        __import__("asyncio").run(provider.initiate_call("+15551230000", "https://x"))
