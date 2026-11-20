"""S3 and MinIO asynchronous object storage client using aioboto3."""

import aioboto3
from botocore.exceptions import ClientError

from procure_core.config import Settings, get_settings
from procure_core.log_config import get_logger

logger = get_logger(__name__)


class S3StorageClient:
    """Async S3-compatible client managing bucket lifecycle and presigned access."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.session = aioboto3.Session()
        self.bucket = self.settings.S3_BUCKET_ATTACHMENTS

    def _get_client_kwargs(self) -> dict[str, str]:
        kwargs: dict[str, str] = {
            "service_name": "s3",
            "region_name": self.settings.S3_REGION_NAME,
            "aws_access_key_id": self.settings.S3_ACCESS_KEY_ID,
            "aws_secret_access_key": self.settings.S3_SECRET_ACCESS_KEY,
        }
        if self.settings.effective_s3_endpoint_url:
            kwargs["endpoint_url"] = self.settings.effective_s3_endpoint_url
        return kwargs

    async def ensure_bucket_exists(self, bucket: str | None = None) -> None:
        """Create bucket if in development mode and it does not exist."""
        target_bucket = bucket or self.bucket
        async with self.session.client(**self._get_client_kwargs()) as s3:
            try:
                await s3.head_bucket(Bucket=target_bucket)
            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code")
                if error_code in ("404", "NoSuchBucket") and self.settings.APP_ENV == "development":
                    logger.info(
                        f"Auto-creating private bucket '{target_bucket}' in development mode"
                    )
                    await s3.create_bucket(Bucket=target_bucket)
                else:
                    raise

    async def upload_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str,
        bucket: str | None = None,
    ) -> str:
        """Upload binary data with content-type to private storage."""
        target_bucket = bucket or self.bucket
        async with self.session.client(**self._get_client_kwargs()) as s3:
            await s3.put_object(
                Bucket=target_bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        return key

    async def download_bytes(self, key: str, bucket: str | None = None) -> bytes:
        """Download binary data for key."""
        target_bucket = bucket or self.bucket
        async with self.session.client(**self._get_client_kwargs()) as s3:
            response = await s3.get_object(Bucket=target_bucket, Key=key)
            async with response["Body"] as stream:
                data = await stream.read()
                return bytes(data)

    async def generate_presigned_url(
        self,
        key: str,
        bucket: str | None = None,
        expires_in_seconds: int = 3600,
    ) -> str:
        """Generate presigned GET URL for client retrieval."""
        target_bucket = bucket or self.bucket
        async with self.session.client(**self._get_client_kwargs()) as s3:
            url: str = await s3.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": target_bucket, "Key": key},
                ExpiresIn=expires_in_seconds,
            )
            return url
