"""WhatsApp outbound sender worker.

Consumes WhatsAppSendTask items from the in-process queue, delivers
messages via the WhatsApp Gateway POST /send endpoint with retry and
exponential backoff, chunks long messages, and updates outbound message
status in the database.

Requirements: Req 1.9, 1.10
Design: Webhook Handling Subsystem, Property 3
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import httpx
import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import MessageOutbound
from app.observability.audit_logger import AuditLogger
from app.schemas.whatsapp import OutboundSendRequest, WhatsAppSendTask
from app.workers.queue import WorkerQueue

logger = structlog.stdlib.get_logger(__name__)

# Constants
MAX_CHUNK_SIZE = 4096
MAX_ATTEMPTS = 3
BACKOFF_DELAYS = [1.0, 2.0, 4.0]  # seconds; capped at 8s
REQUEST_TIMEOUT = 10.0  # seconds per attempt


class WhatsAppSenderWorker:
    """Consumes WhatsAppSendTask items and delivers them via the gateway.

    Features:
    - 10s timeout per HTTP attempt
    - Exponential backoff: 1s, 2s, 4s (capped 8s), max 3 attempts
    - Chunking for messages over 4096 chars
    - Updates messages_outbound.status and attempts
    - On terminal failure: persists 'failed' status and emits audit record
    """

    def __init__(
        self,
        *,
        queue: WorkerQueue[WhatsAppSendTask],
        session_factory: async_sessionmaker[AsyncSession],
        audit_logger: AuditLogger,
        gateway_url: str,
        gateway_token: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._queue = queue
        self._session_factory = session_factory
        self._audit_logger = audit_logger
        self._gateway_url = gateway_url.rstrip("/")
        self._gateway_token = gateway_token
        self._http_client = http_client
        self._running = False

    async def start(self) -> None:
        """Run the worker loop. Blocks until cancelled."""
        self._running = True
        logger.info("whatsapp_sender_worker_started")
        try:
            while self._running:
                task = await self._queue.get()
                try:
                    await self._process_task(task)
                except Exception as exc:
                    logger.error(
                        "whatsapp_sender_unhandled_error",
                        outbound_message_id=str(task.outbound_message_id),
                        error=str(exc),
                    )
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            logger.info("whatsapp_sender_worker_cancelled")
            raise

    def stop(self) -> None:
        """Signal the worker to stop after the current task."""
        self._running = False

    async def _process_task(self, task: WhatsAppSendTask) -> None:
        """Process a single send task, handling chunking."""
        chunks = self._chunk_message(task.text_body)
        total_attempts = 0
        last_error: str | None = None
        whatsapp_message_id: str | None = None

        for chunk_index, chunk_text in enumerate(chunks):
            # Each chunk gets its own idempotency key
            idempotency_key = (
                task.idempotency_key
                if len(chunks) == 1
                else f"{task.idempotency_key}__chunk_{chunk_index}"
            )

            success, attempts, error, msg_id = await self._send_with_retry(
                to_phone=task.to_phone_e164,
                body=chunk_text,
                idempotency_key=idempotency_key,
            )
            total_attempts += attempts
            last_error = error

            if success and msg_id:
                # Use the message_id from the last successful chunk
                whatsapp_message_id = msg_id

            if not success:
                # Terminal failure on this chunk — mark entire message as failed
                await self._update_status_failed(
                    outbound_message_id=task.outbound_message_id,
                    attempts=total_attempts,
                    last_error=last_error or "unknown_error",
                )
                await self._emit_failure_audit(task, total_attempts, last_error)
                return

        # All chunks sent successfully
        await self._update_status_sent(
            outbound_message_id=task.outbound_message_id,
            whatsapp_message_id=whatsapp_message_id,
            attempts=total_attempts,
        )

    async def _send_with_retry(
        self,
        *,
        to_phone: str,
        body: str,
        idempotency_key: str,
    ) -> tuple[bool, int, str | None, str | None]:
        """Attempt to send a message with retry and backoff.

        Returns (success, attempts_used, last_error, message_id).
        """
        client = self._http_client or httpx.AsyncClient()
        should_close = self._http_client is None

        try:
            last_error: str | None = None
            for attempt in range(MAX_ATTEMPTS):
                try:
                    response = await client.post(
                        f"{self._gateway_url}/send",
                        json=OutboundSendRequest(
                            to=to_phone,
                            body=body,
                            idempotency_key=idempotency_key,
                        ).model_dump(),
                        headers={
                            "Authorization": f"Bearer {self._gateway_token}",
                            "Content-Type": "application/json",
                        },
                        timeout=REQUEST_TIMEOUT,
                    )

                    if response.status_code == 200:
                        data = response.json()
                        msg_id = data.get("message_id")
                        return (True, attempt + 1, None, msg_id)

                    # Non-success response
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    logger.warning(
                        "whatsapp_send_non_success",
                        attempt=attempt + 1,
                        status_code=response.status_code,
                        to_phone=to_phone,
                    )

                except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError) as exc:
                    last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                    logger.warning(
                        "whatsapp_send_error",
                        attempt=attempt + 1,
                        error=last_error,
                        to_phone=to_phone,
                    )

                # Backoff before next attempt (skip after last attempt)
                if attempt < MAX_ATTEMPTS - 1:
                    delay = min(BACKOFF_DELAYS[attempt], 8.0)
                    await asyncio.sleep(delay)

            # All attempts exhausted
            return (False, MAX_ATTEMPTS, last_error, None)
        finally:
            if should_close:
                await client.aclose()

    @staticmethod
    def _chunk_message(text: str) -> list[str]:
        """Split a message into chunks of at most MAX_CHUNK_SIZE characters.

        Tries to split on newline boundaries when possible.
        """
        if len(text) <= MAX_CHUNK_SIZE:
            return [text]

        chunks: list[str] = []
        remaining = text

        while remaining:
            if len(remaining) <= MAX_CHUNK_SIZE:
                chunks.append(remaining)
                break

            # Try to find a newline to split on within the chunk size
            split_pos = remaining.rfind("\n", 0, MAX_CHUNK_SIZE)
            if split_pos == -1 or split_pos == 0:
                # No good newline boundary; hard split at max size
                split_pos = MAX_CHUNK_SIZE

            chunks.append(remaining[:split_pos])
            remaining = remaining[split_pos:].lstrip("\n")

        return chunks

    async def _update_status_sent(
        self,
        *,
        outbound_message_id: uuid.UUID,
        whatsapp_message_id: str | None,
        attempts: int,
    ) -> None:
        """Update the outbound message to 'sent' status."""
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            stmt = (
                update(MessageOutbound)
                .where(MessageOutbound.id == outbound_message_id)
                .values(
                    status="sent",
                    whatsapp_message_id=whatsapp_message_id,
                    attempts=attempts,
                    sent_at=now,
                    last_status_at=now,
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def _update_status_failed(
        self,
        *,
        outbound_message_id: uuid.UUID,
        attempts: int,
        last_error: str,
    ) -> None:
        """Update the outbound message to 'failed' status."""
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            stmt = (
                update(MessageOutbound)
                .where(MessageOutbound.id == outbound_message_id)
                .values(
                    status="failed",
                    attempts=attempts,
                    last_error=last_error[:500],  # Truncate to avoid overflow
                    last_status_at=now,
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def _emit_failure_audit(
        self,
        task: WhatsAppSendTask,
        attempts: int,
        last_error: str | None,
    ) -> None:
        """Emit an audit record for a terminal send failure."""
        await self._audit_logger.emit(
            actor="whatsapp_sender_worker",
            event_type="outbound_send_failure",
            conversation_id=task.conversation_id,
            input_redacted={
                "outbound_message_id": str(task.outbound_message_id),
                "to_phone_e164": task.to_phone_e164,
                "attempts": attempts,
            },
            error_code=last_error[:200] if last_error else "unknown_error",
        )
