"""Tenant member invitation API routes."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_core.db import get_async_session
from procure_core.email import get_email_sender
from procure_core.models.auth import (
    CredentialModel,
    InvitationModel,
    UserModel,
    UserRoleAssignmentModel,
)
from procure_core.security.audit_chain import AuditHashChainManager
from procure_core.security.passwords import hash_password, validate_password_strength
from procure_core.security.tokens import create_random_token, hash_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["invitations"])

VALID_ROLES = {
    "requester",
    "approver",
    "finance_admin",
    "security_reviewer",
    "legal_reviewer",
    "tenant_admin",
}


# =============================================================================
# Schemas
# =============================================================================


class CreateInvitationRequest(BaseModel):
    email: EmailStr
    role: str = Field(min_length=2, max_length=64)


class InvitationResponse(BaseModel):
    invitation_id: str
    tenant_id: str
    email: str
    role: str
    invite_token: str
    invitation_token: str = ""
    expires_at: datetime
    message: str


class AcceptInvitationRequest(BaseModel):
    token: str
    password: str
    full_name: str = Field(default="", max_length=255)


class AcceptInvitationResponse(BaseModel):
    user_id: str
    tenant_id: str
    email: str
    role: str
    message: str


# =============================================================================
# Endpoints
# =============================================================================


@router.post(
    "/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    payload: CreateInvitationRequest,
    auth_context: AuthContext = Depends(require_permission("invitations:create")),
    session: AsyncSession = Depends(get_async_session),
) -> InvitationResponse:
    """Create and dispatch an invitation to join the tenant organization."""
    role = payload.role.strip().lower()
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid invitation role '{role}'. Allowed roles: {sorted(VALID_ROLES)}",
        )

    # Tenant is strictly taken from the verified token claim!
    tenant_id = auth_context.tenant_id
    normalized_email = payload.email.strip().lower()

    # Check if user already exists in this tenant
    stmt = select(UserModel).where(
        UserModel.tenant_id == tenant_id,
        UserModel.email == normalized_email,
        UserModel.is_active.is_(True),
    )
    res = await session.execute(stmt)
    if res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User with email '{normalized_email}' already exists in tenant.",
        )

    # Issue invitation token
    raw_token, token_hash = create_random_token()
    inviter_id = uuid.UUID(auth_context.user_id) if len(auth_context.user_id) == 36 else None

    invitation = InvitationModel(
        tenant_id=tenant_id,
        email=normalized_email,
        role=role,
        token_hash=token_hash,
        invited_by=inviter_id,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    session.add(invitation)
    await session.flush()

    # Dispatch email
    email_sender = get_email_sender()
    await email_sender.send_email(
        to=normalized_email,
        subject=f"You're invited to join {tenant_id} on Omnea",
        html_content=(
            f"<p>You have been invited as <strong>{role}</strong>.</p>"
            f"<p>Use token: <code>{raw_token}</code> to accept.</p>"
        ),
        text_content=f"You have been invited as {role}. Use token: {raw_token} to accept.",
    )

    # Append audit event
    await AuditHashChainManager.append_entry(
        session=session,
        tenant_id=tenant_id,
        record_type="invitation",
        record_id=str(invitation.id),
        actor_id=auth_context.user_id,
        action="invitation.created",
        payload={"email": normalized_email, "role": role},
    )

    await session.commit()

    return InvitationResponse(
        invitation_id=str(invitation.id),
        tenant_id=tenant_id,
        email=normalized_email,
        role=role,
        invite_token=raw_token,
        invitation_token=raw_token,
        expires_at=invitation.expires_at,
        message=f"Invitation sent to {normalized_email}.",
    )


@router.post(
    "/invitations/accept",
    response_model=AcceptInvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def accept_invitation(
    payload: AcceptInvitationRequest,
    session: AsyncSession = Depends(get_async_session),
) -> AcceptInvitationResponse:
    """Accept invitation, provision tenant user, and assign invited role."""
    try:
        validate_password_strength(payload.password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    t_hash = hash_token(payload.token.strip())
    stmt = select(InvitationModel).where(
        InvitationModel.token_hash == t_hash,
        InvitationModel.consumed_at.is_(None),
    )
    res = await session.execute(stmt)
    invitation = res.scalar_one_or_none()

    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or already consumed invitation token.",
        )

    if datetime.now(UTC) > invitation.expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitation token has expired.",
        )

    # Provision user in the invitation's tenant
    user = UserModel(
        tenant_id=invitation.tenant_id,
        email=invitation.email,
        full_name=payload.full_name.strip(),
        is_verified=True,  # Accepting invitation verifies email
        is_active=True,
    )
    session.add(user)
    await session.flush()

    # Store hashed password
    pwd_hash = hash_password(payload.password)
    cred = CredentialModel(user_id=user.id, password_hash=pwd_hash)
    session.add(cred)

    # Assign invited role
    role_assign = UserRoleAssignmentModel(
        tenant_id=invitation.tenant_id,
        user_id=str(user.id),
        role_name=invitation.role,
        assigned_by=str(invitation.invited_by) if invitation.invited_by else "system_invitation",
    )
    session.add(role_assign)

    # Mark consumed
    invitation.consumed_at = datetime.now(UTC)

    # Cryptographic audit row
    await AuditHashChainManager.append_entry(
        session=session,
        tenant_id=invitation.tenant_id,
        record_type="invitation",
        record_id=str(invitation.id),
        actor_id=str(user.id),
        action="invitation.accepted",
        payload={
            "user_id": str(user.id),
            "email": user.email,
            "role": invitation.role,
        },
    )

    await session.commit()

    return AcceptInvitationResponse(
        user_id=str(user.id),
        tenant_id=user.tenant_id,
        email=user.email,
        role=invitation.role,
        message="Invitation accepted successfully.",
    )
