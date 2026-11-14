"""Queue consumer executing file download, validation, MinIO upload, and session persistence."""

import hashlib
import re
import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from procure_core.config import Settings, get_settings
from procure_core.log_config import get_logger
from procure_core.storage.base import AttachmentReference, generate_storage_key
from procure_core.storage.s3 import S3StorageClient
from procure_ingress.models import InboundMessage, QueueItem
from procure_ingress.queue import PostgresInboundQueue
from procure_ingress.session import ConversationSessionManager

logger = get_logger(__name__)

# Basic PII regex patterns for log redaction
EMAIL_REGEX = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
PHONE_REGEX = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")


def redact_pii(text: str) -> str:
    """Scrub sensitive PII from log strings."""
    redacted = EMAIL_REGEX.sub("[EMAIL_REDACTED]", text)
    return PHONE_REGEX.sub("[PHONE_REDACTED]", redacted)


class InboundQueueConsumer:
    """Consumes enqueued inbound events, fetches and validates attachments, and updates sessions."""

    def __init__(
        self,
        session: AsyncSession,
        storage_client: S3StorageClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.storage = storage_client or S3StorageClient(self.settings)
        self.queue = PostgresInboundQueue(session)
        self.session_manager = ConversationSessionManager(session)

    async def process_item(
        self,
        item: QueueItem,
        bot_token: str | None = None,
    ) -> InboundMessage | None:
        """Process a single enqueued item."""
        try:
            # 1. Parse into normalized InboundMessage
            if not item.normalized_payload:
                logger.error(f"Queue item {item.id} has no normalized payload")
                await self.queue.mark_failed(item.id, "Missing normalized payload")
                return None

            message = InboundMessage.model_validate(item.normalized_payload)

            # 2. Process file attachments if present (e.g. from Slack)
            files = message.raw_metadata.get("files", [])
            if files and bot_token:
                attachments = await self._process_slack_files(
                    files=files,
                    tenant_id=message.tenant_id,
                    bot_token=bot_token,
                )
                message.attachments.extend(attachments)

            # 3. Upsert conversational session
            await self.session_manager.get_or_create_session(
                tenant_id=message.tenant_id,
                channel_type=message.channel.value,
                conversation_id=message.conversation_id,
                thread_id=message.thread_id,
            )

            # 4. Mark queue item as completed
            await self.queue.mark_complete(
                queue_id=item.id,
                normalized_payload=message.model_dump(mode="json"),
            )

            safe_text = redact_pii(message.text)
            logger.info(
                f"Successfully processed {message.channel} message "
                f"from user={message.user_id}: {safe_text[:60]}",
                extra={"tenant_id": message.tenant_id, "channel": message.channel},
            )
            return message

        except Exception as exc:
            logger.error(f"Failed processing queue item {item.id}: {exc}", exc_info=True)
            await self.queue.mark_failed(item.id, str(exc))
            return None

    async def _process_slack_files(
        self,
        files: list[dict[str, Any]],
        tenant_id: str,
        bot_token: str,
    ) -> list[AttachmentReference]:
        """Download, validate, hash, and upload Slack files to private S3."""
        attachments: list[AttachmentReference] = []

        async with httpx.AsyncClient(timeout=30.0) as http_client:
            for file_info in files:
                url_private = file_info.get("url_private_download") or file_info.get("url_private")
                if not url_private:
                    continue

                filename = file_info.get("name", "attachment")
                mimetype = file_info.get("mimetype", "application/octet-stream")

                # Content-type allowlist check
                if mimetype not in self.settings.ALLOWED_ATTACHMENT_TYPES:
                    logger.warning(
                        f"Rejected attachment {filename}: unsupported MIME type {mimetype}"
                    )
                    continue

                # Authenticated download from Slack
                headers = {"Authorization": f"Bearer {bot_token}"}
                response = await http_client.get(url_private, headers=headers)
                if response.status_code != 200:
                    logger.warning(
                        f"Failed to download Slack file {filename}: status {response.status_code}"
                    )
                    continue

                data = response.content
                size_bytes = len(data)

                # Size cap check
                if size_bytes > self.settings.ATTACHMENT_MAX_SIZE_BYTES:
                    logger.warning(f"Rejected attachment {filename}: size {size_bytes} exceeds cap")
                    continue

                # Compute SHA256 checksum
                checksum = hashlib.sha256(data).hexdigest()

                # Generate secure key {tenant}/{yyyy}/{mm}/{uuid} (never user's filename)
                file_uuid = uuid.uuid4()
                storage_key = generate_storage_key(tenant_id, file_uuid)

                # Upload to MinIO/S3
                await self.storage.upload_bytes(
                    key=storage_key,
                    data=data,
                    content_type=mimetype,
                )

                ref = AttachmentReference(
                    id=file_uuid,
                    filename=filename,
                    content_type=mimetype,
                    size_bytes=size_bytes,
                    storage_key=storage_key,
                    checksum_sha256=checksum,
                )
                attachments.append(ref)
                logger.info(f"Stored attachment {filename} at {storage_key} ({size_bytes} bytes)")

        return attachments
