"""Tests for health check endpoints (/healthz and /readyz).

Validates:
- /healthz returns 200 with status "ok" and a ts field (no DB I/O).
- /readyz returns 200 when both DB and checkpointer are reachable.
- /readyz returns 503 with dependency "db" when DB is unavailable.
- /readyz returns 503 with dependency "checkpointer" when checkpointer is unavailable.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.health import router


@pytest.fixture()
def app_with_healthy_deps() -> FastAPI:
    """Create a FastAPI app with healthy DB and checkpointer mocks."""
    app = FastAPI()
    app.include_router(router)

    # Mock session factory that returns a working session
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=None)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value = mock_session

    # Mock checkpointer connection
    mock_checkpointer_conn = AsyncMock()
    mock_checkpointer_conn.execute = AsyncMock(return_value=None)

    app.state.session_factory = mock_session_factory
    app.state.checkpointer_conn = mock_checkpointer_conn

    return app


@pytest.fixture()
def app_with_no_db() -> FastAPI:
    """Create a FastAPI app with no DB session factory."""
    app = FastAPI()
    app.include_router(router)

    app.state.session_factory = None
    app.state.checkpointer_conn = AsyncMock()

    return app


@pytest.fixture()
def app_with_db_failure() -> FastAPI:
    """Create a FastAPI app where DB SELECT 1 raises an exception."""
    app = FastAPI()
    app.include_router(router)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("connection refused"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value = mock_session

    app.state.session_factory = mock_session_factory
    app.state.checkpointer_conn = AsyncMock()

    return app


@pytest.fixture()
def app_with_no_checkpointer() -> FastAPI:
    """Create a FastAPI app with healthy DB but no checkpointer connection."""
    app = FastAPI()
    app.include_router(router)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=None)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value = mock_session

    app.state.session_factory = mock_session_factory
    app.state.checkpointer_conn = None

    return app


@pytest.fixture()
def app_with_checkpointer_failure() -> FastAPI:
    """Create a FastAPI app where checkpointer execute raises an exception."""
    app = FastAPI()
    app.include_router(router)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=None)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value = mock_session

    mock_checkpointer_conn = AsyncMock()
    mock_checkpointer_conn.execute = AsyncMock(
        side_effect=Exception("checkpointer unreachable")
    )

    app.state.session_factory = mock_session_factory
    app.state.checkpointer_conn = mock_checkpointer_conn

    return app


@pytest.fixture()
def app_with_db_timeout() -> FastAPI:
    """Create a FastAPI app where DB check times out."""
    app = FastAPI()
    app.include_router(router)

    async def slow_execute(*args, **kwargs):
        await asyncio.sleep(10)  # Exceeds the 5s timeout

    mock_session = AsyncMock()
    mock_session.execute = slow_execute
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value = mock_session

    app.state.session_factory = mock_session_factory
    app.state.checkpointer_conn = AsyncMock()

    return app


@pytest.fixture()
def app_with_checkpointer_timeout() -> FastAPI:
    """Create a FastAPI app where checkpointer check times out."""
    app = FastAPI()
    app.include_router(router)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=None)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_session_factory = MagicMock()
    mock_session_factory.return_value = mock_session

    async def slow_execute(*args, **kwargs):
        await asyncio.sleep(10)  # Exceeds the 5s timeout

    mock_checkpointer_conn = AsyncMock()
    mock_checkpointer_conn.execute = slow_execute

    app.state.session_factory = mock_session_factory
    app.state.checkpointer_conn = mock_checkpointer_conn

    return app


class TestHealthz:
    """Tests for GET /healthz."""

    def test_healthz_returns_200_with_status_ok(self, app_with_healthy_deps: FastAPI):
        client = TestClient(app_with_healthy_deps)
        response = client.get("/healthz")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "ts" in data

    def test_healthz_ts_is_iso_format(self, app_with_healthy_deps: FastAPI):
        client = TestClient(app_with_healthy_deps)
        response = client.get("/healthz")

        data = response.json()
        # Should be a valid ISO timestamp
        from datetime import datetime

        ts = datetime.fromisoformat(data["ts"])
        assert ts is not None

    def test_healthz_no_db_still_returns_200(self, app_with_no_db: FastAPI):
        """healthz must not depend on DB availability."""
        client = TestClient(app_with_no_db)
        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestReadyz:
    """Tests for GET /readyz."""

    def test_readyz_returns_200_when_all_healthy(
        self, app_with_healthy_deps: FastAPI
    ):
        client = TestClient(app_with_healthy_deps)
        response = client.get("/readyz")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "ts" in data

    def test_readyz_returns_503_when_no_session_factory(
        self, app_with_no_db: FastAPI
    ):
        client = TestClient(app_with_no_db)
        response = client.get("/readyz")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["dependency"] == "db"

    def test_readyz_returns_503_when_db_raises(
        self, app_with_db_failure: FastAPI
    ):
        client = TestClient(app_with_db_failure)
        response = client.get("/readyz")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["dependency"] == "db"

    def test_readyz_returns_503_when_no_checkpointer(
        self, app_with_no_checkpointer: FastAPI
    ):
        client = TestClient(app_with_no_checkpointer)
        response = client.get("/readyz")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["dependency"] == "checkpointer"

    def test_readyz_returns_503_when_checkpointer_raises(
        self, app_with_checkpointer_failure: FastAPI
    ):
        client = TestClient(app_with_checkpointer_failure)
        response = client.get("/readyz")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["dependency"] == "checkpointer"

    @patch("app.api.health._PROBE_TIMEOUT_SECONDS", 0.1)
    def test_readyz_returns_503_when_db_times_out(
        self, app_with_db_timeout: FastAPI
    ):
        client = TestClient(app_with_db_timeout)
        response = client.get("/readyz")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["dependency"] == "db"

    @patch("app.api.health._PROBE_TIMEOUT_SECONDS", 0.1)
    def test_readyz_returns_503_when_checkpointer_times_out(
        self, app_with_checkpointer_timeout: FastAPI
    ):
        client = TestClient(app_with_checkpointer_timeout)
        response = client.get("/readyz")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["dependency"] == "checkpointer"
