"""Service health endpoint."""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.db.session import database_is_available
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse | JSONResponse:
    """Report application and database readiness without exposing connection details."""
    settings = get_settings()
    if database_is_available():
        return HealthResponse(status="ok", database="connected", environment=settings.app_env)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=HealthResponse(
            status="degraded", database="unavailable", environment=settings.app_env
        ).model_dump(),
    )
