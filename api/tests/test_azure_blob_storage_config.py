"""Tests for per-organization Azure Blob Storage configuration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.enums import OrganizationConfigurationKey, StorageBackend
from api.routes.organization import router as organization_router
from api.schemas.storage_configuration import (
    AzureBlobStorageRequest,
    AzureBlobStorageTestRequest,
)
from api.services.auth.depends import get_user_with_selected_organization
from api.services.configuration import azure_blob_storage as abs_service
from api.services.configuration.masking import mask_key

SERVICE = "api.services.configuration.azure_blob_storage"

REAL_CS = (
    "DefaultEndpointsProtocol=https;AccountName=myacct;"
    "AccountKey=abcdefghijklmnopqrstuvwxyz0123456789ABCD==;"
    "EndpointSuffix=core.windows.net"
)
REAL_KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCD=="


def _stored_config(**overrides):
    config = {
        "enabled": True,
        "connection_string": REAL_CS,
        "account_name": "",
        "account_url": "",
        "account_key": "",
        "container": "voice-audio",
    }
    config.update(overrides)
    return config


def _row(value):
    return SimpleNamespace(value=value)


# ---------------------------------------------------------------------------
# Service: read / save / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_masked_config_masks_secrets():
    with patch(f"{SERVICE}.db_client", new=AsyncMock()) as mock_db:
        mock_db.get_configuration.return_value = _row(_stored_config())
        response = await abs_service.get_masked_azure_blob_config(7)

    mock_db.get_configuration.assert_awaited_once_with(
        7, OrganizationConfigurationKey.AZURE_BLOB_STORAGE.value
    )
    assert response.configured is True
    assert response.enabled is True
    assert response.connection_string == mask_key(REAL_CS)
    assert response.connection_string != REAL_CS
    assert response.container == "voice-audio"


@pytest.mark.asyncio
async def test_get_masked_config_empty_when_unconfigured():
    with patch(f"{SERVICE}.db_client", new=AsyncMock()) as mock_db:
        mock_db.get_configuration.return_value = None
        response = await abs_service.get_masked_azure_blob_config(7)

    assert response.configured is False
    assert response.enabled is False
    assert response.connection_string == ""


@pytest.mark.asyncio
async def test_save_merges_masked_secrets():
    masked = mask_key(REAL_CS)
    request = AzureBlobStorageRequest(
        enabled=True,
        connection_string=masked,
        container="calls",
    )
    with patch(f"{SERVICE}.db_client", new=AsyncMock()) as mock_db:
        mock_db.get_configuration.return_value = _row(_stored_config())
        response = await abs_service.save_azure_blob_config(7, request)
        saved = mock_db.upsert_configuration.await_args.args[2]

    # Masked placeholder resolves to the real stored secret.
    assert saved["connection_string"] == REAL_CS
    assert saved["container"] == "calls"
    assert response.configured is True


@pytest.mark.asyncio
async def test_save_replaces_with_new_secret():
    request = AzureBlobStorageRequest(enabled=True, connection_string="new-secret")
    with patch(f"{SERVICE}.db_client", new=AsyncMock()) as mock_db:
        mock_db.get_configuration.return_value = _row(_stored_config())
        await abs_service.save_azure_blob_config(7, request)
        saved = mock_db.upsert_configuration.await_args.args[2]

    assert saved["connection_string"] == "new-secret"


@pytest.mark.asyncio
async def test_save_enabled_without_credentials_fails():
    request = AzureBlobStorageRequest(enabled=True)
    with patch(f"{SERVICE}.db_client", new=AsyncMock()) as mock_db:
        mock_db.get_configuration.return_value = None
        with pytest.raises(ValueError, match="connection string"):
            await abs_service.save_azure_blob_config(7, request)
        mock_db.upsert_configuration.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_disabled_without_credentials_ok():
    request = AzureBlobStorageRequest(enabled=False)
    with patch(f"{SERVICE}.db_client", new=AsyncMock()) as mock_db:
        mock_db.get_configuration.return_value = None
        response = await abs_service.save_azure_blob_config(7, request)
        mock_db.upsert_configuration.assert_awaited_once()
    assert response.configured is False


@pytest.mark.asyncio
async def test_delete_config():
    with patch(f"{SERVICE}.db_client", new=AsyncMock()) as mock_db:
        mock_db.delete_configuration.return_value = True
        assert await abs_service.delete_azure_blob_config(7) is True
    mock_db.delete_configuration.assert_awaited_once_with(
        7, OrganizationConfigurationKey.AZURE_BLOB_STORAGE.value
    )


def test_is_effective_requires_enabled_and_credentials():
    assert abs_service.is_azure_blob_effective(None) is False
    assert abs_service.is_azure_blob_effective({}) is False
    assert (
        abs_service.is_azure_blob_effective(
            _stored_config(enabled=False)
        )
        is False
    )
    assert (
        abs_service.is_azure_blob_effective(
            {"enabled": True, "connection_string": ""}
        )
        is False
    )
    assert abs_service.is_azure_blob_effective(_stored_config()) is True
    assert (
        abs_service.is_azure_blob_effective(
            {"enabled": True, "account_name": "myacct", "account_key": REAL_KEY}
        )
        is True
    )


# ---------------------------------------------------------------------------
# Service: resolution order (org > env flag > default)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_prefers_org_config_over_env_flag():
    org_fs = MagicMock()
    with (
        patch(f"{SERVICE}.get_org_azure_fs", new=AsyncMock(return_value=org_fs)),
        patch("api.constants.ENABLE_AZURE_BLOB_STORAGE", True),
    ):
        fs, backend = await abs_service.resolve_artifact_storage(7)

    assert fs is org_fs
    assert backend == StorageBackend.AZURE


@pytest.mark.asyncio
async def test_resolve_falls_back_to_env_flag():
    env_fs = MagicMock()
    with (
        patch(f"{SERVICE}.get_org_azure_fs", new=AsyncMock(return_value=None)),
        patch("api.constants.ENABLE_AZURE_BLOB_STORAGE", True),
        patch(
            f"{SERVICE}.get_storage_for_backend", return_value=env_fs
        ) as mock_backend,
    ):
        fs, backend = await abs_service.resolve_artifact_storage(7)

    mock_backend.assert_called_once_with(StorageBackend.AZURE.value)
    assert fs is env_fs
    assert backend == StorageBackend.AZURE


@pytest.mark.asyncio
async def test_resolve_falls_back_to_default_backend():
    with (
        patch(f"{SERVICE}.get_org_azure_fs", new=AsyncMock(return_value=None)),
        patch("api.constants.ENABLE_AZURE_BLOB_STORAGE", False),
    ):
        fs, backend = await abs_service.resolve_artifact_storage(7)

    assert backend == StorageBackend.get_current_backend()


@pytest.mark.asyncio
async def test_resolve_backend_storage_prefers_org_for_azure():
    org_fs = MagicMock()
    with patch(
        f"{SERVICE}.get_org_azure_fs", new=AsyncMock(return_value=org_fs)
    ):
        assert (
            await abs_service.resolve_backend_storage("azure", 7) is org_fs
        )


@pytest.mark.asyncio
async def test_resolve_backend_storage_global_fallback():
    global_fs = MagicMock()
    with (
        patch(f"{SERVICE}.get_org_azure_fs", new=AsyncMock(return_value=None)),
        patch(
            f"{SERVICE}.get_storage_for_backend", return_value=global_fs
        ) as mock_backend,
    ):
        assert (
            await abs_service.resolve_backend_storage("azure", 7) is global_fs
        )
    mock_backend.assert_called_once_with("azure")


# ---------------------------------------------------------------------------
# Service: connection test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_test_uses_stored_secrets_for_masked_input():
    captured = {}

    def fake_build(candidate):
        captured.update(candidate)
        fs = MagicMock()
        fs.account_name = "myacct"
        fs._container_client.get_container_properties.return_value = (
            SimpleNamespace(name="voice-audio")
        )
        return fs

    request = AzureBlobStorageTestRequest(connection_string=mask_key(REAL_CS))
    with (
        patch(f"{SERVICE}.db_client", new=AsyncMock()) as mock_db,
        patch(f"{SERVICE}.build_azure_blob_fs", side_effect=fake_build),
    ):
        mock_db.get_configuration.return_value = _row(_stored_config())
        result = await abs_service.test_azure_blob_connection(7, request)

    assert captured["connection_string"] == REAL_CS
    assert result == {"container": "voice-audio", "account": "myacct"}


@pytest.mark.asyncio
async def test_connection_test_failure_sanitizes_error():
    def fake_build(candidate):
        raise RuntimeError("boom")

    request = AzureBlobStorageTestRequest(connection_string="bad-secret")
    with (
        patch(f"{SERVICE}.db_client", new=AsyncMock()) as mock_db,
        patch(f"{SERVICE}.build_azure_blob_fs", side_effect=fake_build),
    ):
        mock_db.get_configuration.return_value = None
        with pytest.raises(ValueError, match="Could not connect"):
            await abs_service.test_azure_blob_connection(7, request)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _make_org_app():
    app = FastAPI()
    app.include_router(organization_router)

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.provider_id = "provider-1"
    mock_user.is_superuser = False
    mock_user.selected_organization_id = 11

    app.dependency_overrides[get_user_with_selected_organization] = (
        lambda: mock_user
    )
    return app


def test_route_get_masks_secrets():
    app = _make_org_app()
    masked_response = {
        "enabled": True,
        "connection_string": mask_key(REAL_CS),
        "account_name": "",
        "account_url": "",
        "account_key": "",
        "container": "voice-audio",
        "configured": True,
    }
    with (
        TestClient(app) as client,
        patch(
            "api.routes.organization.get_masked_azure_blob_config",
            new=AsyncMock(return_value=masked_response),
        ) as mock_get,
    ):
        response = client.get("/organizations/storage/azure-blob")

    assert response.status_code == 200
    assert response.json()["connection_string"] == mask_key(REAL_CS)
    mock_get.assert_awaited_once_with(11)


def test_route_save_validation_error_is_422():
    app = _make_org_app()
    with (
        TestClient(app) as client,
        patch(
            "api.routes.organization.save_azure_blob_config",
            new=AsyncMock(side_effect=ValueError("no creds")),
        ),
    ):
        response = client.post(
            "/organizations/storage/azure-blob",
            json={"enabled": True},
        )

    assert response.status_code == 422


def test_route_delete_missing_is_404():
    app = _make_org_app()
    with (
        TestClient(app) as client,
        patch(
            "api.routes.organization.delete_azure_blob_config",
            new=AsyncMock(return_value=False),
        ),
    ):
        response = client.delete("/organizations/storage/azure-blob")

    assert response.status_code == 404
