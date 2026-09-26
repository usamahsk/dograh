"""Pydantic schemas for per-organization Azure Blob Storage configuration.

Mirrors the Langfuse-credentials pattern: secrets are masked on read
(``mask_key``) and merged back on write (``is_mask_of``), so the UI can
round-trip a configuration without ever seeing the stored secret again.
"""

from pydantic import BaseModel, Field


class AzureBlobStorageRequest(BaseModel):
    """Incoming org Azure Blob Storage settings from the UI."""

    enabled: bool = True
    connection_string: str = ""
    account_name: str = ""
    account_url: str = ""
    account_key: str = ""
    container: str = "voice-audio"


class AzureBlobStorageResponse(BaseModel):
    """Org Azure Blob Storage settings with secrets masked."""

    enabled: bool = False
    connection_string: str = ""
    account_name: str = ""
    account_url: str = ""
    account_key: str = ""
    container: str = "voice-audio"
    configured: bool = False


class AzureBlobStorageTestRequest(BaseModel):
    """Credentials to validate. Empty secret fields fall back to stored values."""

    connection_string: str = ""
    account_name: str = ""
    account_url: str = ""
    account_key: str = ""
    container: str = Field(default="voice-audio", min_length=1)


class AzureBlobStorageTestResponse(BaseModel):
    ok: bool = True
    container: str = ""
    account: str = ""
