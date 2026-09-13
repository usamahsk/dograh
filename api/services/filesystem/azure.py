import asyncio
import inspect
import mimetypes
from datetime import datetime, timedelta, timezone
from typing import Any, BinaryIO, Dict, Optional

from loguru import logger

from .base import BaseFileSystem


class AzureBlobFileSystem(BaseFileSystem):
    """Azure Blob Storage implementation of the filesystem interface.

    Auth resolution (first match wins):
    1. ``connection_string`` — full Azure Storage connection string.
    2. ``credential`` — any object accepted by the SDK as ``credential``
       (e.g. an account key string or, later, a Managed Identity credential
       such as ``DefaultAzureCredential``) together with ``account_url``.
    3. ``account_key`` + ``account_url`` (or ``account_name``, from which the
       standard ``https://<name>.blob.core.windows.net`` URL is derived).

    The ``container_name`` parameter makes this class reusable beyond call
    artifacts — e.g. a future log-archival subsystem can simply instantiate
    ``AzureBlobFileSystem(container_name="logs", ...)`` with the same
    account credentials.

    Signed (SAS) URLs are generated locally via HMAC and need no network
    access, but they require the account **key** — they are unavailable when
    authenticating with a keyless credential (e.g. Managed Identity). In that
    case plain blob URLs are returned instead.
    """

    def __init__(
        self,
        container_name: str = "voice-audio",
        connection_string: Optional[str] = None,
        account_name: Optional[str] = None,
        account_url: Optional[str] = None,
        account_key: Optional[str] = None,
        credential: Optional[Any] = None,
    ):
        # Imported here so the SDK is only required when this backend is used.
        from azure.storage.blob import BlobServiceClient

        if not account_url and account_name:
            account_url = f"https://{account_name}.blob.core.windows.net"

        resolved_credential: Optional[Any] = credential or account_key

        if connection_string:
            self._service_client = BlobServiceClient.from_connection_string(
                connection_string
            )
        elif account_url and resolved_credential is not None:
            self._service_client = BlobServiceClient(
                account_url=account_url, credential=resolved_credential
            )
        else:
            raise ValueError(
                "AzureBlobFileSystem requires either a connection string "
                "(AZURE_STORAGE_CONNECTION_STRING) or an account URL/name "
                "(AZURE_STORAGE_ACCOUNT_URL / AZURE_STORAGE_ACCOUNT_NAME) "
                "plus a credential (AZURE_STORAGE_ACCOUNT_KEY)."
            )

        self.container_name = container_name
        # Resolved account name, used for SAS generation. May be None when a
        # keyless credential is used (SAS URLs then unavailable).
        self.account_name: Optional[str] = account_name or (
            self._service_client.account_name or None
        )
        # Raw account key, if provided — required for SAS URL generation.
        self.account_key: Optional[str] = (
            account_key if isinstance(account_key, str) else None
        )
        if isinstance(credential, str) and self.account_key is None:
            self.account_key = credential

        self._container_client = self._service_client.get_container_client(
            container_name
        )

        # Ensure the container exists (mirrors MinioFileSystem bucket setup).
        # Failures are non-fatal here so import-time initialization in
        # restricted environments doesn't crash the process.
        try:
            self._container_client.create_container()
            logger.info(
                f"Azure Blob container '{container_name}' ready "
                f"(account: {self.account_name})"
            )
        except Exception as e:
            logger.debug(f"Azure Blob container setup note: {e}")

    def _blob_client(self, file_path: str):
        return self._container_client.get_blob_client(file_path)

    @staticmethod
    def _inline_overrides(file_path: str) -> Dict[str, str]:
        """Content-type/disposition overrides so browsers render artifacts
        inline instead of downloading them (mirrors S3FileSystem)."""
        if file_path.endswith(".txt"):
            return {
                "content_type": "text/plain",
                "content_disposition": "inline",
            }
        elif file_path.endswith(".wav"):
            return {
                "content_type": "audio/wav",
                "content_disposition": "inline",
            }
        elif file_path.endswith(".mp3"):
            return {
                "content_type": "audio/mpeg",
                "content_disposition": "inline",
            }
        return {}

    def _signed_url(
        self,
        file_path: str,
        expiration: int,
        permission_kwargs: Dict[str, bool],
        **sas_overrides: str,
    ) -> str:
        """Build a SAS URL (offline HMAC signing — no network needed)."""
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        if not self.account_name or not self.account_key:
            raise ValueError(
                "SAS URL generation requires the storage account key; "
                "falling back to unsigned URL."
            )
        sas_token = generate_blob_sas(
            account_name=self.account_name,
            container_name=self.container_name,
            blob_name=file_path,
            account_key=self.account_key,
            permission=BlobSasPermissions(**permission_kwargs),
            expiry=datetime.now(timezone.utc) + timedelta(seconds=expiration),
            **sas_overrides,
        )
        return f"{self._blob_client(file_path).url}?{sas_token}"

    async def acreate_file(self, file_path: str, content: BinaryIO) -> bool:
        try:
            from azure.storage.blob import ContentSettings

            # Accept both sync (e.g. BytesIO) and async streams.
            data = content.read()
            if inspect.isawaitable(data):
                data = await data
            content_type, _ = mimetypes.guess_type(file_path)

            def _upload():
                self._blob_client(file_path).upload_blob(
                    data,
                    overwrite=True,
                    content_settings=(
                        ContentSettings(content_type=content_type)
                        if content_type
                        else None
                    ),
                )

            await asyncio.to_thread(_upload)
            return True
        except Exception as e:
            logger.error(f"Error creating Azure blob '{file_path}': {e}")
            return False

    async def aupload_file(self, local_path: str, destination_path: str) -> bool:
        try:
            from azure.storage.blob import ContentSettings

            content_type, _ = mimetypes.guess_type(destination_path)

            def _upload():
                with open(local_path, "rb") as f:
                    self._blob_client(destination_path).upload_blob(
                        f,
                        overwrite=True,
                        content_settings=(
                            ContentSettings(content_type=content_type)
                            if content_type
                            else None
                        ),
                    )

            await asyncio.to_thread(_upload)
            return True
        except Exception as e:
            logger.error(
                f"Error uploading '{local_path}' to Azure blob "
                f"'{destination_path}': {e}"
            )
            return False

    async def aget_signed_url(
        self,
        file_path: str,
        expiration: int = 3600,
        force_inline: bool = False,
        use_internal_endpoint: bool = False,
    ) -> Optional[str]:
        """Generate a read SAS URL for the blob.

        Azure exposes a single endpoint, so ``use_internal_endpoint`` is
        accepted for interface compatibility but has no effect.
        """
        try:
            overrides = self._inline_overrides(file_path) if force_inline else {}
            return await asyncio.to_thread(
                self._signed_url,
                file_path,
                expiration,
                {"read": True},
                **overrides,
            )
        except ValueError:
            # Keyless auth (e.g. Managed Identity): return the plain blob URL
            # and rely on container-level anonymous read access instead.
            logger.debug(
                f"No account key available for SAS; returning unsigned URL "
                f"for '{file_path}'"
            )
            return self._blob_client(file_path).url
        except Exception as e:
            logger.error(f"Error generating Azure SAS URL for '{file_path}': {e}")
            return None

    async def aget_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get Azure blob metadata."""
        try:
            props = await asyncio.to_thread(
                self._blob_client(file_path).get_blob_properties
            )
            etag = props.etag.strip('"') if props.etag else None
            content_type = None
            if props.content_settings:
                content_type = props.content_settings.content_type
            return {
                "size": props.size,
                "created_at": props.creation_time,
                "modified_at": props.last_modified,
                "etag": etag,
                "content_type": content_type,
                "storage_class": props.blob_tier,
            }
        except Exception:
            return None

    async def aget_presigned_put_url(
        self,
        file_path: str,
        expiration: int = 900,
        content_type: str = "text/csv",
        max_size: int = 10_485_760,
    ) -> Optional[str]:
        """Generate a write SAS URL for direct browser upload.

        Note: unlike S3 presigned PUTs, blob SAS URLs cannot enforce
        ``content_type``/``max_size`` server-side; callers should validate
        uploads after the fact.
        """
        try:
            return await asyncio.to_thread(
                self._signed_url,
                file_path,
                expiration,
                {"write": True, "create": True},
            )
        except ValueError:
            logger.error(
                f"Cannot generate Azure upload URL for '{file_path}': "
                f"account key required for SAS signing."
            )
            return None
        except Exception as e:
            logger.error(
                f"Error generating Azure upload URL for '{file_path}': {e}"
            )
            return None

    async def adownload_file(self, source_path: str, local_path: str) -> bool:
        try:

            def _download():
                with open(local_path, "wb") as f:
                    self._blob_client(source_path).download_blob().readinto(f)

            await asyncio.to_thread(_download)
            return True
        except Exception as e:
            logger.error(
                f"Error downloading Azure blob '{source_path}' to "
                f"'{local_path}': {e}"
            )
            return False

    async def acopy_file(
        self, source_path: str, destination_path: str
    ) -> bool:
        """Copy a blob within the container (server-side copy)."""
        try:

            def _copy():
                source_url = self._blob_client(source_path).url
                poller = self._blob_client(destination_path).start_copy_from_url(
                    source_url
                )
                poller.wait()

            await asyncio.to_thread(_copy)
            return True
        except Exception as e:
            logger.error(
                f"Error copying Azure blob '{source_path}' to "
                f"'{destination_path}': {e}"
            )
            return False
