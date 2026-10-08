from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from app.core.redis import get_redis
from app.db.session import async_session_factory

router = APIRouter()


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def readiness() -> dict[str, object]:
    checks: dict[str, str] = {}

    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["mysql"] = "ok"
    except Exception:
        checks["mysql"] = "unavailable"

    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"

    if any(value != "ok" for value in checks.values()):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "checks": checks},
        )

    return {"status": "ready", "checks": checks}

