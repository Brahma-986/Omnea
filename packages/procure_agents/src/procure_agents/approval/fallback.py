"""Approver assignment, self-approval fallback resolution, and 403 audit logging."""

from __future__ import annotations

from datetime import datetime


class AuthorizationRefusedError(Exception):
    """Raised when an actor lacks authorization to approve or sign off on a request."""

    def __init__(self, message: str, actor_id: str, fallback_id: str) -> None:
        super().__init__(message)
        self.actor_id = actor_id
        self.fallback_id = fallback_id


def resolve_approver_with_fallback(
    requester_id: str,
    primary_id: str,
    delegate_id: str | None,
    delegate_valid_from: datetime | None,
    delegate_valid_until: datetime | None,
    backup_id: str,
    admin_id: str = "tenant_admin",
    as_of: datetime | None = None,
) -> tuple[str, str]:
    """Resolves active approver while strictly preventing self-approval.

    Uses snapshot as_of (deterministic, no wall-clock reads).

    Fallback hierarchy:
    1. Primary approver (if primary_id != requester_id)
    2. Active Delegate within valid window (if delegate_id != requester_id)
    3. Designated Backup approver (if backup_id != requester_id)
    4. Tenant Admin (if admin_id != requester_id)
    5. If admin is requester: routes to 'requires_board_escalation'

    Returns:
        (assigned_approver_id, resolution_reason)
    """
    if as_of is None:
        raise ValueError(
            "as_of snapshot timestamp is required; wall-clock datetime.now() is prohibited"
        )

    # 1. Primary check
    if primary_id != requester_id:
        return primary_id, "assigned_primary_approver"

    # 2. Delegate check
    if (
        delegate_id
        and delegate_valid_from
        and delegate_valid_until
        and delegate_valid_from <= as_of <= delegate_valid_until
        and delegate_id != requester_id
    ):
        return delegate_id, "delegate_fallback_due_to_self_approval"

    # 3. Backup check
    if backup_id and backup_id != requester_id:
        return backup_id, "backup_fallback_due_to_self_approval"

    # 4. Admin check
    if admin_id and admin_id != requester_id:
        return admin_id, "admin_fallback_due_to_self_approval"

    # 5. Admin is also the requester -> requires board escalation
    return "board_audit_committee", "requires_board_escalation"


def format_auth_refused_idempotency_key(
    tenant_id: str,
    request_id: str,
    actor_id: str,
    step_name: str,
    action: str,
    round_num: int,
) -> str:
    """Deterministic idempotency key for 403 refusal audit records."""
    return f"{tenant_id}:{request_id}:auth_refused:{actor_id}:{step_name}:{action}:{round_num}"


def authorize_approver(
    actor_id: str,
    requester_id: str,
    assigned_approver_id: str,
    is_admin: bool = False,
) -> bool:
    """Validates that actor is authorized to sign off.

    Rules:
    1. Requester is NEVER authorized to approve their own request (Self-approval block).
    2. Actor must match assigned_approver_id or be an authenticated tenant admin.
    """
    if actor_id == requester_id:
        return False
    if is_admin:
        return True
    return actor_id == assigned_approver_id


def can_resubmit(
    actor_id: str,
    requester_id: str,
    is_admin: bool = False,
) -> bool:
    """Only the original requester or an authorized tenant admin can resubmit an expired request."""
    return actor_id == requester_id or is_admin
