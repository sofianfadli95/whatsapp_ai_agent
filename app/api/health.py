"""Health check endpoints.

- GET /healthz: liveness probe, no DB I/O, responds in under 1s.
- GET /readyz: readiness probe, runs SELECT 1 against the DB and a
  checkpointer reachability probe with asyncio.wait_for (5s timeout each).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter()

_PROBE_TIMEOUT_SECONDS = 5.0


@router.get("/healthz")
async def healthz() -> dict:
    """Liveness check. No DB I/O. Returns 200 always."""
    return {
        "status": "ok",
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness check.

    Checks:
    1. Database reachability via SELECT 1 (5s timeout).
    2. Checkpointer reachability via SELECT 1 on the checkpointer
       connection (5s timeout).

    Returns 200 if both pass, 503 with the failing dependency otherwise.
    """
    # --- DB check ---
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "dependency": "db"},
        )

    try:
        async with session_factory() as session:
            await asyncio.wait_for(
                session.execute(text("SELECT 1")),
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "dependency": "db"},
        )

    # --- Checkpointer check ---
    checkpointer_conn = getattr(request.app.state, "checkpointer_conn", None)
    if checkpointer_conn is None:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "dependency": "checkpointer"},
        )

    try:
        await asyncio.wait_for(
            checkpointer_conn.execute("SELECT 1"),
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "dependency": "checkpointer"},
        )

    # --- Both healthy ---
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    )
