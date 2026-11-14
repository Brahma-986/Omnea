"""Object storage module re-exports."""

from procure_core.storage.base import (
    AttachmentReference,
    ObjectStorageClient,
    generate_storage_key,
)
from procure_core.storage.s3 import S3StorageClient

__all__ = [
    "AttachmentReference",
    "ObjectStorageClient",
    "S3StorageClient",
    "generate_storage_key",
]
