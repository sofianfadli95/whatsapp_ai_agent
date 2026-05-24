"""Pydantic schemas for WhatsApp messaging.

Defines the data models used by the outbound sender worker and
the WhatsApp webhook handler.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, Field


class OutboundSendRequest(BaseModel):
    """Request body sent to the WhatsApp Gateway POST /send endpoint."""

    to: str = Field(description="Recipient phone number in E.164 format")
    body: str = Field(description="Message text body")
    idempotency_key: str = Field(description="Unique key for deduplication")


class OutboundSendResponse(BaseModel):
    """Response from the WhatsApp Gateway POST /send endpoint."""

    status: str = Field(description="Send status: 'sent' or error indicator")
    message_id: Optional[str] = Field(
        default=None, description="WhatsApp message ID assigned by the gateway"
    )


@dataclass
class WhatsAppSendTask:
    """Task payload for the in-process send queue.

    Represents a single outbound message to be delivered via the WhatsApp Gateway.
    """

    outbound_message_id: uuid.UUID
    conversation_id: uuid.UUID
    to_phone_e164: str
    text_body: str
    idempotency_key: str = field(default_factory=lambda: str(uuid.uuid4()))
