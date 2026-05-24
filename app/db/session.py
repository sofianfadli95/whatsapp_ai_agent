"""SQLAlchemy async engine and session factory.

Provides the engine, session factory, and a FastAPI dependency for
request-scoped async sessions.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine as _create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine with pool_pre_ping enabled."""
    return _create_async_engine(
        database_url,
        pool_pre_ping=True,
        echo=False,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a request-scoped async session.

    Usage in route handlers:
        from fastapi import Depends, Request

        async def my_route(request: Request):
            session_factory = request.app.state.session_factory
            async for session in get_session(session_factory):
                ...
    """
    async with session_factory() as session:
        yield session
