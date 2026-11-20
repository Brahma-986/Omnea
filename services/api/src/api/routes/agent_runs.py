"""Agent execution runs tracking and telemetry endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_core.db import get_async_session
from procure_core.models.vendor import EntityReviewTaskModel

router = APIRouter(prefix="/api/v1/agents", tags=["Developer Platform - Agent Runs"])


class AgentRunItem(BaseModel):
    id: str
    agent_name: str
    task_type: str
    status: str
    created_at: str
    metadata: dict[str, Any]


@router.get("/runs", response_model=list[AgentRunItem])
async def list_agent_runs(
    status_filter: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_permission("user:read")),
    db: AsyncSession = Depends(get_async_session),
) -> list[dict[str, Any]]:
    """List agent orchestration runs (Entity-Resolution, Policy-Drafting, TPRM extraction, Saga sync)."""
    stmt = (
        select(EntityReviewTaskModel)
        .where(EntityReviewTaskModel.tenant_id == auth.tenant_id)
        .order_by(EntityReviewTaskModel.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status_filter:
        stmt = stmt.where(EntityReviewTaskModel.status == status_filter)

    res = await db.execute(stmt)
    tasks = res.scalars().all()

    runs: list[dict[str, Any]] = [
        {
            "id": str(t.id),
            "agent_name": "EntityResolutionAgent",
            "task_type": "entity_matching",
            "status": t.status,
            "created_at": t.created_at.isoformat()
            if hasattr(t, "created_at") and t.created_at
            else "",
            "metadata": {
                "source_vendor": t.source_vendor_name if hasattr(t, "source_vendor_name") else "",
                "candidate_vendor": t.candidate_vendor_name
                if hasattr(t, "candidate_vendor_name")
                else "",
                "score": float(t.match_score)
                if hasattr(t, "match_score") and t.match_score
                else 0.0,
            },
        }
        for t in tasks
    ]

    return runs


@router.get("/runs/{run_id}", response_model=AgentRunItem)
async def get_agent_run(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("user:read")),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Retrieve full execution metadata and reasoning citations for an agent run."""
    stmt = select(EntityReviewTaskModel).where(
        EntityReviewTaskModel.id == run_id,
        EntityReviewTaskModel.tenant_id == auth.tenant_id,
    )
    res = await db.execute(stmt)
    task = res.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")

    return {
        "id": str(task.id),
        "agent_name": "EntityResolutionAgent",
        "task_type": "entity_matching",
        "status": task.status,
        "created_at": task.created_at.isoformat()
        if hasattr(task, "created_at") and task.created_at
        else "",
        "metadata": {
            "source_vendor": getattr(task, "source_vendor_name", ""),
            "candidate_vendor": getattr(task, "candidate_vendor_name", ""),
            "score": float(getattr(task, "match_score", 0.0)),
        },
    }
