"""In-process asyncio worker queue.

Provides a generic WorkerQueue protocol and an AsyncioWorkerQueue
implementation backed by asyncio.Queue. This interface can later be
swapped for Cloud Tasks or Pub/Sub without changing consumer code.
"""

from __future__ import annotations

import asyncio
from typing import Generic, Protocol, TypeVar

T = TypeVar("T")


class WorkerQueue(Protocol[T]):
    """Protocol for a typed async task queue."""

    async def put(self, item: T) -> None:
        """Enqueue an item for processing."""
        ...

    async def get(self) -> T:
        """Dequeue the next item. Blocks until one is available."""
        ...

    def task_done(self) -> None:
        """Mark the most recently dequeued item as processed."""
        ...

    def empty(self) -> bool:
        """Return True if the queue has no pending items."""
        ...


class AsyncioWorkerQueue(Generic[T]):
    """In-process asyncio.Queue-backed implementation of WorkerQueue."""

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)

    async def put(self, item: T) -> None:
        """Enqueue an item for processing."""
        await self._queue.put(item)

    async def get(self) -> T:
        """Dequeue the next item. Blocks until one is available."""
        return await self._queue.get()

    def task_done(self) -> None:
        """Mark the most recently dequeued item as processed."""
        self._queue.task_done()

    def empty(self) -> bool:
        """Return True if the queue has no pending items."""
        return self._queue.empty()
