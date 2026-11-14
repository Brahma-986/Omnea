"""Consolidated domain model re-exports."""

from procure_core.models.audit import AuditLogModel, AuditLogSchema
from procure_core.models.base import Base, TenantMixin, TimestampMixin
from procure_core.models.intent import ProcurementIntentModel, ProcurementIntentSchema
from procure_core.models.request import (
    ApprovalModel,
    ApprovalSchema,
    RequestModel,
    RequestSchema,
)
from procure_core.models.vendor import (
    ContractModel,
    EntityReviewTaskModel,
    IntentVendorResolutionModel,
    Neo4jOutboxModel,
    ResolutionDecisionModel,
    SpendTransactionModel,
    TenantResolutionSettingsModel,
    VendorAliasModel,
    VendorEmbeddingModel,
    VendorModel,
    VendorSecurityReviewModel,
)
from procure_core.models.workspace import (
    WorkspaceInstallationModel,
    WorkspaceInstallationSchema,
)

__all__ = [
    "Base",
    "TenantMixin",
    "TimestampMixin",
    "RequestModel",
    "ApprovalModel",
    "AuditLogModel",
    "WorkspaceInstallationModel",
    "ProcurementIntentModel",
    "RequestSchema",
    "ApprovalSchema",
    "AuditLogSchema",
    "WorkspaceInstallationSchema",
    "ProcurementIntentSchema",
    "TenantResolutionSettingsModel",
    "VendorModel",
    "VendorAliasModel",
    "VendorEmbeddingModel",
    "SpendTransactionModel",
    "ContractModel",
    "VendorSecurityReviewModel",
    "ResolutionDecisionModel",
    "IntentVendorResolutionModel",
    "EntityReviewTaskModel",
    "Neo4jOutboxModel",
]
