"""Send-reply node — enqueues the outbound WhatsApp message.

This node does NOT call WhatsApp directly. It enqueues a WhatsAppSendTask
into the worker queue, then returns. The graph never blocks on outbound
network IO inside the conversation transaction.

Requirements: Reqs 3, 4, 5, 6, 7, 10
Design: LangGraph Agent Design, Property 24
"""

from __future__ import annotations

from typing import Any, Protocol

from app.agent.state import ConversationState


class WhatsAppSendTask:
    """A task representing an outbound WhatsApp message to be sent."""

    def __init__(self, phone_e164: str, body: str, conversation_id: str) -> None:
        self.phone_e164 = phone_e164
        self.body = body
        self.conversation_id = conversation_id


class SendQueue(Protocol):
    """Protocol for the outbound message queue."""

    async def put(self, item: Any) -> None: ...


def build_send_reply_node(
    send_queue: SendQueue,
) -> Any:
    """Build the send_reply node function.

    Args:
        send_queue: The worker queue for outbound WhatsApp messages.

    Returns:
        An async function suitable for use as a LangGraph node.
    """

    async def send_reply(state: ConversationState) -> dict[str, Any]:
        """Enqueue the reply for sending via WhatsApp. Never blocks on outbound IO."""
        reply_text = state.get("reply_text")
        phone_e164 = state.get("phone_e164", "")
        conversation_id = state.get("conversation_id", "")

        if not reply_text:
            # Nothing to send — this shouldn't normally happen
            return {"reply_enqueued": False}

        # Non-text message type gets a standard reply
        if state.get("inbound_message_type") != "text" and not state.get("escalation_flag"):
            reply_text = (
                "Maaf, saat ini kami hanya mendukung pesan teks. "
                "Silakan kirim pesan dalam bentuk teks."
            )

        task = WhatsAppSendTask(
            phone_e164=phone_e164,
            body=reply_text,
            conversation_id=conversation_id,
        )

        await send_queue.put(task)

        return {
            "reply_enqueued": True,
            "reply_text": reply_text,
        }

    return send_reply
