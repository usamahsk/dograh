"""Per-organization Azure Blob Storage configuration for call artifacts.

Clients manage their own storage account from the UI (Settings page) instead
of touching deployment env vars. Storage follows the Langfuse-credentials
pattern: one row in ``organization_configurations`` under the
``AZURE_BLOB_STORAGE`` key, secrets masked on read and merged back on write.

Resolution order for call artifacts (recordings + transcript):
1. The run's organization has Azure Blob enabled + credentials -> org account.
2. ``ENABLE_AZURE_BLOB_STORAGE`` env flag -> deployment-wide Azure account.
3. Otherwise the deployment's default backend (MinIO/S3), unchanged.
"""

import asyncio
from typing import Any, Optional

from loguru import logger

from api.db import db_client
from api.enums import OrganizationConfigurationKey, StorageBackend
from api.schemas.storage_configuration import (
    AzureBlobStorageRequest,
    AzureBlobStorageResponse,
    AzureBlobStorageTestRequest,
)
from api.services.configuration.masking import is_mask_of, mask_key
from api.services.filesystem.azure import AzureBlobFileSystem
from api.services.storage import (
    get_current_storage_backend,
    get_storage_for_backend,
    storage_fs,
)

CONFIG_KEY = OrganizationConfigurationKey.AZURE_BLOB_STORAGE.value
DEFAULT_CONTAINER = "voice-audio"


def _has_usable_credentials(config: dict[str, Any]) -> bool:
    """True when the config can authenticate against Azure Blob Storage."""
    if config.get("connection_string"):
        return True
    if config.get("account_url") or config.get("account_name"):
        return True
    return False


def is_azure_blob_effective(config: dict[str, Any] | None) -> bool:
    """True when artifacts for this org should go to its Azure account."""
    return bool(config and config.get("enabled") and _has_usable_credentials(config))


async def get_azure_blob_config_value(
    organization_id: int,
) -> dict[str, Any] | None:
    """Return the raw stored config (server-side only, contains secrets)."""
    row = await db_client.get_configuration(organization_id, CONFIG_KEY)
    if not row or not row.value:
        return None
    return dict(row.value)


async def get_masked_azure_blob_config(
    organization_id: int,
) -> AzureBlobStorageResponse:
    config = await get_azure_blob_config_value(organization_id)
    if not config:
        return AzureBlobStorageResponse()
    return AzureBlobStorageResponse(
        enabled=bool(config.get("enabled", False)),
        connection_string=(
            mask_key(config.get("connection_string", ""))
            if config.get("connection_string")
            else ""
        ),
        account_name=config.get("account_name", ""),
        account_url=config.get("account_url", ""),
        account_key=(
            mask_key(config.get("account_key", ""))
            if config.get("account_key")
            else ""
        ),
        container=config.get("container", DEFAULT_CONTAINER) or DEFAULT_CONTAINER,
        configured=_has_usable_credentials(config),
    )


async def save_azure_blob_config(
    organization_id: int,
    request: AzureBlobStorageRequest,
) -> AzureBlobStorageResponse:
    """Merge masked secrets against stored values, validate, and persist."""
    existing = await get_azure_blob_config_value(organization_id) or {}

    connection_string = request.connection_string or ""
    account_key = request.account_key or ""
    if existing:
        if is_mask_of(
            connection_string, existing.get("connection_string", "")
        ):
            connection_string = existing.get("connection_string", "")
        if is_mask_of(account_key, existing.get("account_key", "")):
            account_key = existing.get("account_key", "")

    container = (request.container or "").strip() or DEFAULT_CONTAINER

    config_value = {
        "enabled": request.enabled,
        "connection_string": connection_string,
        "account_name": (request.account_name or "").strip(),
        "account_url": (request.account_url or "").strip(),
        "account_key": account_key,
        "container": container,
    }

    if request.enabled and not _has_usable_credentials(config_value):
        raise ValueError(
            "Provide an Azure connection string or an account URL/name "
            "to enable Azure Blob Storage."
        )

    await db_client.upsert_configuration(
        organization_id,
        CONFIG_KEY,
        config_value,
    )
    return await get_masked_azure_blob_config(organization_id)


async def delete_azure_blob_config(organization_id: int) -> bool:
    return await db_client.delete_configuration(organization_id, CONFIG_KEY)


def build_azure_blob_fs(config: dict[str, Any]) -> AzureBlobFileSystem:
    """Instantiate an AzureBlobFileSystem from a stored config dict."""
    return AzureBlobFileSystem(
        container_name=config.get("container") or DEFAULT_CONTAINER,
        connection_string=config.get("connection_string") or None,
        account_name=config.get("account_name") or None,
        account_url=config.get("account_url") or None,
        account_key=config.get("account_key") or None,
    )


async def get_org_azure_fs(
    organization_id: int | None,
) -> Optional[AzureBlobFileSystem]:
    """Org Azure filesystem when the org has it enabled, else None."""
    if not organization_id:
        return None
    try:
        config = await get_azure_blob_config_value(organization_id)
    except Exception as exc:
        logger.error(
            f"Failed to load Azure Blob config for org {organization_id}: {exc}"
        )
        return None
    if not is_azure_blob_effective(config):
        return None
    try:
        return build_azure_blob_fs(config)
    except Exception as exc:
        logger.error(
            f"Failed to build Azure Blob fs for org {organization_id}: {exc}"
        )
        return None


async def resolve_artifact_storage(organization_id: int | None):
    """Filesystem + backend label for call artifacts, org-aware.

    Org account first, then the deployment-wide env flag, then default.
    """
    from api.constants import ENABLE_AZURE_BLOB_STORAGE

    org_fs = await get_org_azure_fs(organization_id)
    if org_fs is not None:
        return org_fs, StorageBackend.AZURE
    if ENABLE_AZURE_BLOB_STORAGE:
        return get_storage_for_backend(StorageBackend.AZURE.value), StorageBackend.AZURE
    return storage_fs, get_current_storage_backend()


async def resolve_backend_storage(backend: str, organization_id: int | None):
    """Filesystem for a recorded backend, preferring the org's Azure account.

    Used by download paths: rows stamped ``azure`` resolve to the owning org's
    account when configured, otherwise to the deployment-wide Azure backend
    (env-flag deployments keep working).
    """
    if backend == StorageBackend.AZURE.value:
        org_fs = await get_org_azure_fs(organization_id)
        if org_fs is not None:
            return org_fs
    return get_storage_for_backend(backend)


async def resolve_run_backend_storage(backend: str, run_id: int | None):
    """Filesystem for a run's recorded backend, preferring the run's org Azure.

    Used by download paths: the owning org is resolved from the run, so a
    superuser fetching another org's run still hits the correct account.
    """
    organization_id = None
    if run_id is not None:
        try:
            organization_id = await db_client.get_organization_id_by_workflow_run_id(
                run_id
            )
        except Exception as exc:
            logger.error(
                f"Failed to resolve org for workflow run {run_id}: {exc}"
            )
    return await resolve_backend_storage(backend, organization_id)


async def test_azure_blob_connection(
    organization_id: int,
    request: AzureBlobStorageTestRequest,
) -> dict[str, str]:
    """Validate Azure credentials by reading container properties.

    Empty secret fields fall back to the stored config so the UI can test
    without re-typing secrets. Raises ValueError with a sanitized message
    (never echoes secrets) on failure.
    """
    stored = await get_azure_blob_config_value(organization_id) or {}

    connection_string = request.connection_string or ""
    account_key = request.account_key or ""
    if is_mask_of(connection_string, stored.get("connection_string", "")):
        connection_string = stored.get("connection_string", "")
    if is_mask_of(account_key, stored.get("account_key", "")):
        account_key = stored.get("account_key", "")

    candidate = {
        "connection_string": connection_string,
        "account_name": (request.account_name or "").strip()
        or stored.get("account_name", ""),
        "account_url": (request.account_url or "").strip()
        or stored.get("account_url", ""),
        "account_key": account_key,
        "container": (request.container or "").strip()
        or stored.get("container", DEFAULT_CONTAINER)
        or DEFAULT_CONTAINER,
    }
    if not _has_usable_credentials(candidate):
        raise ValueError(
            "Provide an Azure connection string or an account URL/name to test."
        )

    def _check() -> dict[str, str]:
        fs = build_azure_blob_fs(candidate)
        props = fs._container_client.get_container_properties()
        return {
            "container": props.name,
            "account": fs.account_name or "",
        }

    try:
        return await asyncio.to_thread(_check)
    except ValueError:
        raise
    except Exception as exc:
        logger.error(
            f"Azure Blob connection test failed for org {organization_id}: "
            f"{type(exc).__name__}: {exc}"
        )
        raise ValueError(
            "Could not connect to Azure Blob Storage with the provided "
            f"credentials ({type(exc).__name__}). Check the account details "
            "and container name."
        ) from exc
