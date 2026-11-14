"""Review API routes for human-in-the-loop entity resolution reviews."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from procure_context.review.manager import ReviewManager
from procure_core.db import get_async_session
from procure_core.models.vendor import EntityReviewTaskModel

router = APIRouter(prefix="/api/v1/reviews", tags=["Entity Reviews"])


class AuthUser(BaseModel):
    """Authenticated user context."""

    user_id: str
    email: str
    tenant_id: str


async def get_current_user(
    authorization: str | None = Header(None, alias="Authorization"),
    x_tenant_id: str | None = Header(None, alias="x-tenant-id"),
) -> AuthUser:
    """Validate Bearer authentication token and verify tenant boundary."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Token payload format: "user@domain.com:tenant_id" or "token_<user>_<tenant>"
    if ":" in token:
        email, token_tenant = token.split(":", 1)
    elif "_" in token:
        parts = token.split("_")
        email = f"{parts[1]}@omnea.ai" if len(parts) > 1 else "user@omnea.ai"
        token_tenant = parts[2] if len(parts) > 2 else "default"
    else:
        email = f"{token}@omnea.ai"
        token_tenant = token

    # Tenant enforcement: if x-tenant-id is provided, it must match token tenant
    if x_tenant_id and x_tenant_id != token_tenant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Forbidden: Token tenant '{token_tenant}' does not match "
                f"requested tenant '{x_tenant_id}'"
            ),
        )

    return AuthUser(
        user_id=email,
        email=email,
        tenant_id=x_tenant_id or token_tenant,
    )


class ResolveReviewRequest(BaseModel):
    """Payload to resolve an entity review task."""

    action: Literal["merge", "reject", "keep_separate"]
    target_vendor_id: uuid.UUID | None = None
    notes: str | None = None


class ReviewTaskResponse(BaseModel):
    """API response for an entity review task."""

    id: uuid.UUID
    tenant_id: str
    task_type: str
    status: str
    provisional_vendor_id: uuid.UUID
    candidate_vendor_id: uuid.UUID | None
    source_name: str
    confidence: float
    resolved_by: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.get(
    "/tasks",
    summary="List pending entity review tasks",
    response_model=list[ReviewTaskResponse],
)
async def list_review_tasks(
    status_filter: str = "pending",
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
) -> list[ReviewTaskResponse]:
    """Fetch review tasks for the authenticated user's tenant."""
    q = (
        select(EntityReviewTaskModel)
        .where(
            and_(
                EntityReviewTaskModel.tenant_id == user.tenant_id,
                EntityReviewTaskModel.status == status_filter,
            )
        )
        .order_by(EntityReviewTaskModel.created_at.desc())
    )
    res = await db.execute(q)
    tasks = res.scalars().all()
    return [
        ReviewTaskResponse(
            id=t.id,
            tenant_id=t.tenant_id,
            task_type=t.task_type,
            status=t.status,
            provisional_vendor_id=t.provisional_vendor_id,
            candidate_vendor_id=t.candidate_vendor_id,
            source_name=t.source_name,
            confidence=float(t.confidence),
            resolved_by=t.resolved_by,
            metadata=t.metadata_ or {},
        )
        for t in tasks
    ]


@router.post(
    "/tasks/{task_id}/resolve",
    summary="Resolve an entity review task (merge or reject)",
    response_model=ReviewTaskResponse,
)
async def resolve_review_task(
    task_id: uuid.UUID,
    payload: ResolveReviewRequest,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
) -> ReviewTaskResponse:
    """Resolve an entity review task: merge into candidate or reject and keep separate."""
    # Fetch task and assert tenant ownership
    q = select(EntityReviewTaskModel).where(EntityReviewTaskModel.id == task_id)
    res = await db.execute(q)
    task = res.scalar_one_or_none()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review task {task_id} not found",
        )

    # Tenant isolation guard (403 if tenant mismatch)
    if task.tenant_id != user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Cannot resolve task belonging to another tenant",
        )

    if task.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task {task_id} is already resolved with status '{task.status}'",
        )

    review_mgr = ReviewManager(tenant_id=user.tenant_id)

    if payload.action == "merge":
        target_id = payload.target_vendor_id or task.candidate_vendor_id
        if not target_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="target_vendor_id is required to perform merge",
            )
        await review_mgr.merge_vendors(
            db,
            source_id=task.provisional_vendor_id,
            target_id=target_id,
            actor=user.email,
        )
        task.status = "approved_merge"
        task.resolved_by = user.email
    elif payload.action in ("reject", "keep_separate"):
        task.status = "rejected_keep_separate"
        task.resolved_by = user.email
        db.add(task)
        await db.flush()

    await db.commit()
    await db.refresh(task)

    return ReviewTaskResponse(
        id=task.id,
        tenant_id=task.tenant_id,
        task_type=task.task_type,
        status=task.status,
        provisional_vendor_id=task.provisional_vendor_id,
        candidate_vendor_id=task.candidate_vendor_id,
        source_name=task.source_name,
        confidence=float(task.confidence),
        resolved_by=task.resolved_by,
        metadata=task.metadata_ or {},
    )
