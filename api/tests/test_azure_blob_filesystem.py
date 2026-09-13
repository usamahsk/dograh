"""Unit tests for the Azure Blob Storage filesystem backend.

All tests run offline: SAS signing is local HMAC (no network), and SDK
clients are mocked. No Azure account or emulator required.
"""

import base64
import io
import os
from unittest.mock import MagicMock, patch

import pytest

# api.constants reads these at import time; provide dummies so this module
# stays importable without a real environment.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from api.enums import StorageBackend  # noqa: E402
from api.services.filesystem.azure import AzureBlobFileSystem  # noqa: E402

FAKE_ACCOUNT = "testaccount"
# base64("fake-key-0123456789") — valid key material for offline SAS signing.
FAKE_KEY = base64.b64encode(b"fake-key-0123456789").decode()
FAKE_URL = f"https://{FAKE_ACCOUNT}.blob.core.windows.net"


def make_fs(container_name="voice-audio", **kwargs):
    """Build a backend with a mocked service client (no network)."""
    kwargs.setdefault("account_name", FAKE_ACCOUNT)
    kwargs.setdefault("account_key", FAKE_KEY)
    with patch("azure.storage.blob.BlobServiceClient") as mock_client_cls:
        fs = AzureBlobFileSystem(container_name=container_name, **kwargs)
    fs._service_client = mock_client_cls.return_value
    fs._container_client = MagicMock()
    return fs


def test_requires_credentials():
    with pytest.raises(ValueError, match="connection string"):
        with patch("azure.storage.blob.BlobServiceClient"):
            AzureBlobFileSystem()


def test_account_name_derives_url():
    with patch("azure.storage.blob.BlobServiceClient") as mock_cls:
        AzureBlobFileSystem(account_name="myacct", account_key=FAKE_KEY)
    _, kwargs = mock_cls.call_args
    assert kwargs["account_url"] == "https://myacct.blob.core.windows.net"


def test_custom_container_for_logging_reuse():
    """A logging subsystem can reuse this class with another container."""
    fs = make_fs(container_name="logs")
    assert fs.container_name == "logs"


def test_signed_url_offline():
    """Read SAS URLs are generated locally (HMAC) — no network needed."""
    fs = make_fs()
    blob = MagicMock()
    blob.url = f"{FAKE_URL}/voice-audio/calls/abc.wav"
    fs._container_client.get_blob_client.return_value = blob

    import asyncio

    url = asyncio.get_event_loop().run_until_complete(
        fs.aget_signed_url("calls/abc.wav", expiration=600)
    )
    assert url.startswith(f"{FAKE_URL}/voice-audio/calls/abc.wav?")
    assert "sig=" in url
    assert "se=" in url  # expiry


def test_signed_url_force_inline_txt():
    fs = make_fs()
    blob = MagicMock()
    blob.url = f"{FAKE_URL}/voice-audio/calls/abc.txt"
    fs._container_client.get_blob_client.return_value = blob

    import asyncio

    url = asyncio.get_event_loop().run_until_complete(
        fs.aget_signed_url("calls/abc.txt", force_inline=True)
    )
    # response-content-disposition=inline override present in SAS
    assert "rscd=inline" in url


def test_signed_url_falls_back_without_key():
    """Keyless auth (e.g. Managed Identity) returns the plain blob URL."""
    with patch("azure.storage.blob.BlobServiceClient"):
        fs = AzureBlobFileSystem(account_url=FAKE_URL, credential=object())
    blob = MagicMock()
    blob.url = f"{FAKE_URL}/voice-audio/calls/abc.wav"
    fs._container_client = MagicMock()
    fs._container_client.get_blob_client.return_value = blob

    import asyncio

    url = asyncio.get_event_loop().run_until_complete(
        fs.aget_signed_url("calls/abc.wav")
    )
    assert url == f"{FAKE_URL}/voice-audio/calls/abc.wav"


def test_presigned_put_url_offline():
    fs = make_fs()
    blob = MagicMock()
    blob.url = f"{FAKE_URL}/voice-audio/uploads/a.csv"
    fs._container_client.get_blob_client.return_value = blob

    import asyncio

    url = asyncio.get_event_loop().run_until_complete(
        fs.aget_presigned_put_url("uploads/a.csv")
    )
    assert url is not None
    assert "sig=" in url
    # write+create permissions encoded in the signed resource/permissions
    assert "sp=" in url


def test_create_and_upload_and_download(tmp_path):
    import asyncio

    fs = make_fs()
    blob = MagicMock()
    fs._container_client.get_blob_client.return_value = blob

    loop = asyncio.get_event_loop()
    assert loop.run_until_complete(
        fs.acreate_file("a.txt", io.BytesIO(b"hello"))
    ) is True
    blob.upload_blob.assert_called()

    local = tmp_path / "in.wav"
    local.write_bytes(b"RIFF....")
    assert loop.run_until_complete(
        fs.aupload_file(str(local), "calls/in.wav")
    ) is True

    def _readinto(f):
        f.write(b"audio-bytes")

    blob.download_blob.return_value.readinto.side_effect = _readinto
    out = tmp_path / "out.wav"
    assert loop.run_until_complete(
        fs.adownload_file("calls/in.wav", str(out))
    ) is True
    assert out.read_bytes() == b"audio-bytes"


def test_copy_and_metadata():
    import asyncio

    fs = make_fs()
    dest = MagicMock()
    dest.start_copy_from_url.return_value.wait.return_value = None
    src = MagicMock()
    src.url = f"{FAKE_URL}/voice-audio/a.wav"
    fs._container_client.get_blob_client.side_effect = lambda p: (
        src if p == "a.wav" else dest
    )

    loop = asyncio.get_event_loop()
    assert loop.run_until_complete(fs.acopy_file("a.wav", "b.wav")) is True
    dest.start_copy_from_url.assert_called_once_with(src.url)

    props = MagicMock()
    props.etag = '"0x8ABC"'
    props.size = 1234
    props.creation_time = "created"
    props.last_modified = "modified"
    props.content_settings.content_type = "audio/wav"
    props.blob_tier = "Hot"
    src.get_blob_properties.return_value = props
    meta = loop.run_until_complete(fs.aget_file_metadata("a.wav"))
    assert meta == {
        "size": 1234,
        "created_at": "created",
        "modified_at": "modified",
        "etag": "0x8ABC",
        "content_type": "audio/wav",
        "storage_class": "Hot",
    }


def test_backend_selection(monkeypatch):
    import api.constants as constants

    monkeypatch.setattr(constants, "ENABLE_AZURE_BLOB_STORAGE", True)
    monkeypatch.setattr(constants, "ENABLE_AWS_S3", True)
    assert StorageBackend.get_current_backend() == StorageBackend.AZURE

    monkeypatch.setattr(constants, "ENABLE_AZURE_BLOB_STORAGE", False)
    assert StorageBackend.get_current_backend() == StorageBackend.S3

    monkeypatch.setattr(constants, "ENABLE_AWS_S3", False)
    assert StorageBackend.get_current_backend() == StorageBackend.MINIO
