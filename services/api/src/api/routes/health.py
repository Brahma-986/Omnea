"""Health check route."""

from fastapi import APIRouter, Depends, Response, status

from procure_core.config import Settings, get_settings
from procure_core.health import HealthResponse, check_all_health

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service and dependency health check",
    responses={
        200: {"description": "All dependencies are healthy"},
        503: {"description": "One or more dependencies are degraded/unreachable"},
    },
)
async def get_health(
    response: Response,
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    """Evaluate health across Postgres, Neo4j, and Temporal."""
    health = await check_all_health(settings)
    if health.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return health
