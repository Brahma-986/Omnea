"""Base SQLAlchemy declarative class and shared mixins."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base SQLAlchemy declarative class."""

    pass


class TenantMixin:
    """Tenant isolation mixin."""

    tenant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="tenant_default",
        index=True,
    )


class TimestampMixin:
    """Standard timestamp mixin."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
