"""Unit tests for app/db/session.py — async engine, session factory, and get_session dependency.

Validates:
- create_engine returns an AsyncEngine with pool_pre_ping enabled.
- create_session_factory returns an async_sessionmaker with expire_on_commit=False.
- get_session yields a request-scoped session that can execute SELECT 1.

Requirement: 13.7
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.session import create_engine, create_session_factory, get_session

# Use an in-memory SQLite async engine for testing (no real DB needed).
_TEST_DB_URL = "sqlite+aiosqlite://"


class TestCreateEngine:
    """Tests for create_engine function."""

    def test_returns_async_engine(self):
        engine = create_engine(_TEST_DB_URL)
        assert isinstance(engine, AsyncEngine)

    def test_pool_pre_ping_enabled(self):
        # SQLite uses StaticPool which doesn't expose pre_ping directly.
        # Verify the engine was created with pool_pre_ping=True by inspecting
        # the sync engine's pool configuration (works for QueuePool-based engines).
        # For SQLite StaticPool, we verify the underlying sync engine dialect option.
        engine = create_engine(_TEST_DB_URL)
        # The pool_pre_ping flag is stored on the engine itself in SQLAlchemy 2.0
        assert engine.sync_engine.pool._pre_ping is True

    def test_echo_disabled(self):
        engine = create_engine(_TEST_DB_URL)
        assert engine.echo is False


class TestCreateSessionFactory:
    """Tests for create_session_factory function."""

    def test_returns_async_sessionmaker(self):
        engine = create_engine(_TEST_DB_URL)
        factory = create_session_factory(engine)
        assert isinstance(factory, async_sessionmaker)

    def test_expire_on_commit_false(self):
        engine = create_engine(_TEST_DB_URL)
        factory = create_session_factory(engine)
        # async_sessionmaker stores kw args; expire_on_commit is in kw
        assert factory.kw.get("expire_on_commit") is False

    def test_session_class_is_async_session(self):
        engine = create_engine(_TEST_DB_URL)
        factory = create_session_factory(engine)
        assert factory.class_ is AsyncSession


class TestGetSession:
    """Tests for get_session FastAPI dependency."""

    @pytest.mark.asyncio
    async def test_yields_async_session(self):
        engine = create_engine(_TEST_DB_URL)
        factory = create_session_factory(engine)

        async for session in get_session(factory):
            assert isinstance(session, AsyncSession)

    @pytest.mark.asyncio
    async def test_session_executes_select_1(self):
        engine = create_engine(_TEST_DB_URL)
        factory = create_session_factory(engine)

        async for session in get_session(factory):
            result = await session.execute(text("SELECT 1"))
            value = result.scalar()
            assert value == 1

    @pytest.mark.asyncio
    async def test_session_is_request_scoped(self):
        """Verify get_session yields exactly one session per invocation."""
        engine = create_engine(_TEST_DB_URL)
        factory = create_session_factory(engine)

        sessions: list[AsyncSession] = []
        async for session in get_session(factory):
            sessions.append(session)

        # The generator yields exactly once (request-scoped)
        assert len(sessions) == 1
