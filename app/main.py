"""FastAPI application factory with lifespan management.

Validates settings, initializes DB engine + session factory,
initializes the LangGraph PostgresSaver checkpointer, starts
in-process workers, and on shutdown drains them with a 10s budget.

Wires all routers and sets up structured logging middleware that
injects request_id into structlog context.

Requirements: Req 12.6, 12.7, 12.9, 12.10, 13.9
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.api.health import router as health_router
from app.config import Settings, StartupConfigError
from app.db.session import create_engine, create_session_factory
from app.observability.logging import configure_logging, generate_request_id

logger = structlog.stdlib.get_logger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown logic."""

    # 1. Configure structured logging first
    configure_logging()

    # 2. Validate settings (fail-fast)
    try:
        settings = Settings()  # type: ignore[call-arg]
    except (StartupConfigError, Exception) as exc:
        # Use print here since structlog may not be fully wired yet
        print(f"FATAL: Configuration validation failed: {exc}", file=sys.stderr)
        sys.exit(1)

    app.state.settings = settings

    # 3. Initialize DB engine + session factory
    engine = None
    session_factory = None
    try:
        engine = create_engine(settings.DATABASE_URL)
        session_factory = create_session_factory(engine)
        app.state.engine = engine
        app.state.session_factory = session_factory
        logger.info("db_engine_initialized", database_url="***")
    except Exception as exc:
        # Allow app to start even if DB is not available (local dev)
        logger.warning("db_engine_init_failed", error=str(exc))
        app.state.engine = None
        app.state.session_factory = None

    # 4. Initialize LangGraph PostgresSaver checkpointer
    checkpointer = None
    checkpointer_conn = None
    try:
        import psycopg

        # Convert SQLAlchemy-style URL to psycopg-compatible format
        # postgresql+asyncpg://... -> postgresql://...
        pg_url = settings.DATABASE_URL.replace("+asyncpg", "")

        checkpointer_conn = await psycopg.AsyncConnection.connect(
            pg_url, autocommit=True
        )
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        checkpointer = AsyncPostgresSaver(conn=checkpointer_conn)
        await checkpointer.setup()
        app.state.checkpointer = checkpointer
        app.state.checkpointer_conn = checkpointer_conn
        logger.info("checkpointer_initialized")
    except Exception as exc:
        # Allow app to start even if checkpointer setup fails (local dev, DB not ready)
        logger.warning("checkpointer_init_failed", error=str(exc))
        app.state.checkpointer = None
        app.state.checkpointer_conn = None
        # Clean up partial connection if it was opened
        if checkpointer_conn is not None:
            try:
                await checkpointer_conn.close()
            except Exception:
                pass

    # 5. Start in-process workers (placeholder)
    # Workers will be implemented in a later phase. For now, we store
    # a list of worker tasks that can be populated later.
    worker_tasks: list[asyncio.Task[Any]] = []
    app.state.worker_tasks = worker_tasks
    logger.info("workers_started", count=len(worker_tasks))

    yield

    # --- Shutdown ---

    # 6. Drain workers with a 10s budget
    if worker_tasks:
        logger.info("draining_workers", count=len(worker_tasks))
        for task in worker_tasks:
            task.cancel()
        # Wait up to 10 seconds for workers to finish
        done, pending = await asyncio.wait(
            worker_tasks, timeout=10.0, return_when=asyncio.ALL_COMPLETED
        )
        if pending:
            logger.warning("workers_drain_timeout", pending_count=len(pending))

    # 7. Dispose DB engine
    if engine is not None:
        await engine.dispose()
        logger.info("db_engine_disposed")

    # 8. Close checkpointer connection if needed
    checkpointer_conn = getattr(app.state, "checkpointer_conn", None)
    if checkpointer_conn is not None:
        try:
            await checkpointer_conn.close()
            logger.info("checkpointer_connection_closed")
        except Exception:
            pass

    logger.info("shutdown_complete")


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Middleware that generates a request_id per request and binds it to structlog context."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = generate_request_id()

        # Bind request_id to structlog context for this request
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Store on request state for access in route handlers
        request.state.request_id = request_id

        response = await call_next(request)

        # Include request_id in response headers for traceability
        response.headers["X-Request-ID"] = request_id

        return response


# ---------------------------------------------------------------------------
# App Factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title="WhatsApp Sales Agent",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Add middleware
    application.add_middleware(RequestIdMiddleware)

    # Wire routers
    application.include_router(health_router, tags=["health"])

    return application


# Module-level app instance for uvicorn: `app.main:app`
app = create_app()
