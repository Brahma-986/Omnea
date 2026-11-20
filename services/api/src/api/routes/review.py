"""Review API routes for human-in-the-loop entity resolution reviews."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_context.review.manager import ReviewManager
from procure_core.db import get_async_session
from procure_core.models.vendor import EntityReviewTaskModel

router = APIRouter(prefix="/api/v1/reviews", tags=["Entity Reviews"])


class ResolveReviewRequest(BaseModel):
    """Payload to resolve an entity review task."""

    action: Literal["merge", "reject", "keep_separate", "unmerge"]
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
    auth: AuthContext = Depends(require_permission("review:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[ReviewTaskResponse]:
    """Fetch review tasks for the authenticated user's tenant."""
    q = (
        select(EntityReviewTaskModel)
        .where(
            and_(
                EntityReviewTaskModel.tenant_id == auth.tenant_id,
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
    summary="Resolve an entity review task (merge, reject, or unmerge)",
    response_model=ReviewTaskResponse,
)
async def resolve_review_task(
    task_id: uuid.UUID,
    payload: ResolveReviewRequest,
    auth: AuthContext = Depends(require_permission("review:resolve")),
    db: AsyncSession = Depends(get_async_session),
) -> ReviewTaskResponse:
    """Resolve an entity review task: merge into candidate, reject and keep separate, or unmerge."""
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
    if task.tenant_id != auth.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Cannot resolve task belonging to another tenant",
        )

    review_mgr = ReviewManager(tenant_id=auth.tenant_id)

    if payload.action == "unmerge":
        try:
            await review_mgr.unmerge_vendors(
                session=db,
                source_id=task.provisional_vendor_id,
                actor=auth.email,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        task.status = "rejected_keep_separate"
        task.resolved_by = auth.email
        db.add(task)
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

    if task.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task {task_id} is already resolved with status '{task.status}'",
        )

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
            actor=auth.email,
        )
        task.status = "approved_merge"
        task.resolved_by = auth.email
    elif payload.action in ("reject", "keep_separate"):
        task.status = "rejected_keep_separate"
        task.resolved_by = auth.email
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


@router.post(
    "/tasks/{task_id}/unmerge",
    summary="Reversibly unmerge vendor associated with review task",
    response_model=ReviewTaskResponse,
)
async def unmerge_review_task(
    task_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("review:resolve")),
    db: AsyncSession = Depends(get_async_session),
) -> ReviewTaskResponse:
    """Reversibly unmerge vendor associated with a review task."""
    q = select(EntityReviewTaskModel).where(EntityReviewTaskModel.id == task_id)
    res = await db.execute(q)
    task = res.scalar_one_or_none()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review task {task_id} not found",
        )

    if task.tenant_id != auth.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Cannot unmerge task belonging to another tenant",
        )

    review_mgr = ReviewManager(tenant_id=auth.tenant_id)
    try:
        await review_mgr.unmerge_vendors(
            session=db,
            source_id=task.provisional_vendor_id,
            actor=auth.email,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    task.status = "rejected_keep_separate"
    task.resolved_by = auth.email
    db.add(task)
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
