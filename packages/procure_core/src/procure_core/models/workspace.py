"""Workspace installations ORM model and schemas for tenant resolution."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from procure_core.models.base import Base, TenantMixin, TimestampMixin


class WorkspaceInstallationModel(Base, TenantMixin, TimestampMixin):
    """External platform workspace mapping to an internal tenant."""

    __tablename__ = "workspace_installations"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    external_workspace_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )
    bot_token: Mapped[str] = mapped_column(String(255), nullable=False)
    bot_user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    installation_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    __table_args__ = (
        Index(
            "uq_workspace_platform_external_id",
            "platform",
            "external_workspace_id",
            unique=True,
        ),
    )


class WorkspaceInstallationSchema(BaseModel):
    """Pydantic representation of a workspace installation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: str
    platform: str
    external_workspace_id: str
    bot_user_id: str
    installation_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
