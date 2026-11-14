"""Object storage interfaces and attachment metadata models."""

import uuid
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class AttachmentReference(BaseModel):
    """Reference to an attachment stored in object storage."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    filename: str
    content_type: str
    size_bytes: int = Field(ge=0)
    storage_key: str
    checksum_sha256: str
    presigned_url: str | None = None


def generate_storage_key(tenant_id: str, file_uuid: uuid.UUID | None = None) -> str:
    """Generate object storage key formatted as {tenant}/{yyyy}/{mm}/{uuid}."""
    uid = file_uuid or uuid.uuid4()
    now = datetime.now(UTC)
    return f"{tenant_id}/{now.year:04d}/{now.month:02d}/{uid}"


class ObjectStorageClient(Protocol):
    """Protocol defining object storage operations."""

    async def upload_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str,
        bucket: str | None = None,
    ) -> str:
        """Upload raw bytes and return the object storage key."""
        ...

    async def download_bytes(self, key: str, bucket: str | None = None) -> bytes:
        """Download raw bytes for a given object storage key."""
        ...

    async def generate_presigned_url(
        self,
        key: str,
        bucket: str | None = None,
        expires_in_seconds: int = 3600,
    ) -> str:
        """Generate a presigned read URL for client retrieval."""
        ...

    async def ensure_bucket_exists(self, bucket: str | None = None) -> None:
        """Ensure the specified bucket exists, creating it if configured for dev."""
        ...
