# Design Document

## Overview

The WhatsApp Sales Agent MVP is an event-driven, service-oriented backend that lets online sellers automate sales and customer service through WhatsApp. The system is delivered as a single FastAPI deployable that contains:

- A WhatsApp webhook receiver that turns inbound messages into Conversation events.
- A LangGraph-orchestrated AI agent that classifies intent, calls deterministic tools, and produces replies.
- A set of business services (Catalog, Order, Payment, Logistics, Human Support, Conversation, Audit) with clear seams so each can later be extracted into its own MCP server (`catalog-mcp-service`, `order-mcp-service`, `payment-mcp-service`, `logistics-mcp-service`, `human-support-mcp-service`).
- PostgreSQL with the pgvector extension, used for OLTP storage, audit logs, the LangGraph checkpoint store, and the RAG vector index.
- An in-process asyncio worker layer for outbound WhatsApp sends, logistics preparation, and audit log flushing, behind a swappable interface that can later be replaced with Cloud Tasks or Pub/Sub.

The design encodes the non-negotiable safety rules from the requirements:

- The LLM is never the source of truth for prices, stock, payment status, order status, or shipment status.
- Order creation requires a deterministic `customer_confirmation_token` derived from a cart snapshot.
- Payment status only changes via signature-verified webhooks processed idempotently in a single DB transaction.
- A `paid` order enqueues exactly one logistics task and exactly one customer notification, deduplicated per `order_id`.
- Conversation state is only loaded/committed via the PostgreSQL-backed `LangGraph` checkpointer; load/commit failures escalate transactionally before audit logging.
- Phone numbers are normalized to E.164 before any conversation lookup or create.
- All webhook signatures are verified before any state mutation; raw bodies are captured for HMAC verification.
- Audit log writes are async with a durable failure queue and redact secrets, credentials, and PANs.

This document defines the architecture, components, data model, correctness properties, error handling, and testing strategy that satisfy Requirements 1 through 13.

### Design Goals

1. **Transactional safety**: every state-changing flow has a single, well-defined DB transaction boundary and idempotency key.
2. **Deterministic commerce**: totals, stock, status transitions, tracking numbers come only from services and their persisted records.
3. **MCP-readiness**: business logic lives in `services/` behind a thin tool wrapper, so MCP extraction is a refactor, not a rewrite.
4. **Cloud Run-readiness**: 12-factor configuration, fast cold start, `/healthz` and `/readyz`, no in-memory state.
5. **Observability and auditability**: structured logs with `request_id`, audit records for every state-changing tool, redaction by default.
6. **Property-test friendly**: pure functions for tokens, totals, normalization, and state transitions; service-level integration tests with mocks for external providers.

### Mapping to Requirements (Summary)

| Requirement | Design Components |
| --- | --- |
| 1. WhatsApp webhook + reply | `api/whatsapp.py`, `workers/whatsapp_sender.py`, `repositories/messages.py`, `WhatsApp Gateway (whatsapp_gateway/)` |
| 2. Conversation state + checkpointing | `agent/checkpointer.py` (PostgresSaver), `services/conversation.py`, `utils/phone.py` |
| 3. Catalog Q&A + RAG | `services/catalog.py`, `vectorstore/retriever.py`, `tools/catalog.py`, `agent/nodes/retrieve_rag.py` |
| 4. Recommendation | `agent/nodes/recommend.py`, `tools/catalog.py::search_products` |
| 5. Cart management | `services/catalog.py` (cart submodule), `tools/catalog.py` (cart tools), `repositories/cart.py` |
| 6. Order creation + confirmation token | `services/order.py`, `tools/order.py`, `utils/confirmation_token.py` |
| 7. Payment link | `services/payment.py`, `tools/payment.py`, payment provider client behind interface |
| 8. Verified idempotent payment webhook | `api/payment.py`, `services/payment.py`, `repositories/webhook_events.py`, `repositories/dispatched_actions.py` |
| 9. Simulated logistics | `services/logistics.py`, `workers/logistics_worker.py`, `repositories/shipments.py` |
| 10. Human escalation | `services/human_support.py`, `services/conversation.py`, `api/admin.py`, `agent/nodes/escalate.py` |
| 11. Audit logging | `observability/audit_logger.py`, `repositories/audit.py`, `workers/audit_flusher.py` |
| 12. LLM_Factory + Cloud Run readiness | `agent/llm_factory.py`, `app/config.py`, `api/health.py`, `Dockerfile`, `docker-compose.yml` |
| 13. uv-based local dev | `pyproject.toml`, `uv.lock`, `.env.example`, `scripts/migrate_and_seed.py`, `README.md` |

A more detailed per-requirement mapping appears at the end of the document.

---

## Architecture

### High-Level Architecture

The MVP runs as a single FastAPI process containing the API surface, the LangGraph agent runtime, the service layer, and an in-process asyncio worker layer. PostgreSQL with pgvector is the only stateful store. External providers (WhatsApp, Payment) are reached through HTTP clients in the workers.

```mermaid
flowchart LR
    subgraph External
        WA_GW["WhatsApp Gateway\n(Node.js + Baileys)"]
        PP[Payment Provider Sandbox]
        ADMIN[Admin User]
    end

    subgraph "FastAPI App (Cloud Run)"
        direction TB
        subgraph API["app/api (FastAPI routers)"]
            WH_WA[Inbound Webhook<br/>POST /internal/whatsapp/inbound]
            WH_PAY[Payment Webhook<br/>POST /webhooks/payment]
            HEALTH[/healthz, /readyz/]
            ADM[/admin/conversations/{id}/resume/]
        end

        subgraph AGENT["app/agent (LangGraph)"]
            GRAPH[Conversation Graph]
            CKPT[PostgresSaver Checkpointer]
            LLMF[LLM_Factory]
        end

        subgraph TOOLS["app/tools (LangChain tools)"]
            T_CAT[Catalog Tools]
            T_ORD[Order Tools]
            T_PAY[Payment Tools]
            T_LOG[Logistics Tools]
            T_SUP[Support Tools]
        end

        subgraph SERVICES["app/services (business logic)"]
            S_CAT[CatalogService]
            S_ORD[OrderService]
            S_PAY[PaymentService]
            S_LOG[LogisticsService]
            S_SUP[HumanSupportService]
            S_CONV[ConversationService]
            S_AUD[AuditService]
        end

        subgraph DATA["app/repositories + app/db"]
            REPO[(SQLAlchemy / asyncpg<br/>Repositories)]
        end

        subgraph VECT["app/vectorstore"]
            RAG[RAG Retriever<br/>+ Ingestion]
        end

        subgraph WORKERS["app/workers (asyncio)"]
            W_WA[WhatsApp Sender]
            W_LOG[Logistics Preparer]
            W_AUD[Audit Flusher]
            QUEUE[(In-process asyncio queue<br/>+ DB-backed dispatched_actions)]
        end
    end

    DB[(PostgreSQL + pgvector)]

    WA_GW -->|"POST /internal/whatsapp/inbound\n(Bearer token, internal HTTP)"| WH_WA
    WH_WA --> S_CONV
    WH_WA --> AGENT
    AGENT --> TOOLS
    TOOLS --> SERVICES
    SERVICES --> REPO
    SERVICES --> RAG
    REPO --> DB
    RAG --> DB
    CKPT --> DB
    AGENT --> LLMF
    AGENT --> W_WA
    PP --> WH_PAY
    WH_PAY --> S_PAY
    S_PAY --> QUEUE
    QUEUE --> W_LOG
    QUEUE --> W_WA
    W_WA -->|"POST /send\n(Bearer token)"| WA_GW
    S_AUD --> W_AUD
    W_AUD --> DB
    ADMIN --> ADM
    ADM --> S_CONV
```

### Architectural Style

- **Event-driven inside a single deployable**: webhooks are the events; agent runs and worker tasks are the consumers.
- **Service-oriented seams**: each service module exposes a narrow async interface (`async def add_to_cart(...) -> ToolResult`). Tool wrappers are thin LangChain adapters; MCP extraction means turning the same async interface into an MCP server, the agent only swaps its tool client.
- **Hexagonal-ish boundaries**:
  - Inbound adapters: FastAPI routers, WhatsApp webhook, Payment webhook, Admin endpoints.
  - Outbound adapters: WhatsApp HTTP client, Payment provider HTTP client, all behind interfaces.
  - Core: services + repositories + LangGraph agent + LLM factory.
- **Cloud Run-friendly**: stateless process, configuration only via environment variables, port from `$PORT`, no on-disk session state, in-process queue with at-least-once semantics; for horizontal scaling the queue interface can be swapped to Cloud Tasks (see Background Workers).

### Request Lifecycles

#### Inbound WhatsApp message (happy path)

1. The WhatsApp Gateway (Node.js + Baileys) receives a customer message and forwards it via `POST /internal/whatsapp/inbound` with `Authorization: Bearer <WHATSAPP_GATEWAY_INTERNAL_TOKEN>`.
2. `api/whatsapp.py` verifies the bearer token (constant-time comparison) against `WHATSAPP_GATEWAY_INTERNAL_TOKEN`. On failure → 401, audit `auth_*`.
3. The inbound handler parses the `InboundEvent` payload (Pydantic), normalizes the sender phone number to E.164, persists the inbound message (deduplicated by Baileys message id), and returns `200` within 5 seconds.
4. The handler enqueues an in-process agent task for that conversation thread.
5. The agent task: loads checkpoint via `PostgresSaver`, runs the LangGraph, commits checkpoint, enqueues an outbound reply task.
6. The WhatsApp sender worker delivers the reply with retry/backoff, persists outbound status updates.

#### Payment webhook (happy path)

1. Payment provider delivers `POST /webhooks/payment` with raw JSON.
2. `api/payment.py` verifies the signature using `PAYMENT_WEBHOOK_SECRET` (≤500 ms).
3. Handler parses payload (Pydantic) and calls `PaymentService.process_webhook(event)`.
4. Inside a single DB transaction: lock `payments` row `FOR UPDATE`, insert `payment_webhook_events(webhook_event_id)` with `ON CONFLICT DO NOTHING` (idempotency), update `payments.status`, update `orders.status` if applicable, conditionally insert into `dispatched_actions(order_id, action_type)` for `logistics_prepare` and `payment_confirmation_reply`.
5. On commit, enqueue logistics + outbound reply tasks (only if the dispatched_actions inserts succeeded).
6. Respond `200` within 5 seconds.

### Module / Package Layout

The code follows the structure mandated by `steering/structure.md`:

```
ai-commerce-agent/
├── whatsapp_gateway/           # Node.js + Baileys WhatsApp Gateway microservice
│   ├── src/
│   │   ├── index.ts            # Fastify app entry point
│   │   ├── baileys/
│   │   │   ├── session.ts      # SessionManager singleton (useMultiFileAuthState)
│   │   │   └── inbound.ts      # messages.upsert handler + InboundEvent builder
│   │   ├── forwarder/
│   │   │   └── forwarder.ts    # HTTP forwarder with retry + overflow queue
│   │   ├── middleware/
│   │   │   └── auth.ts         # Bearer-token middleware (constant-time compare)
│   │   ├── routes/
│   │   │   ├── send.ts         # POST /send
│   │   │   ├── qr.ts           # GET /qr
│   │   │   └── health.ts       # GET /healthz, GET /readyz
│   │   └── utils/
│   │       └── phone.ts        # E.164 normalization
│   ├── package.json
│   ├── tsconfig.json
│   ├── Dockerfile
│   └── README.md
├── app/
│   ├── main.py                 # FastAPI app factory, lifespan hooks, router wiring
│   ├── config.py               # Pydantic Settings, env-var validation, fail-fast on startup
│   ├── constants.py            # Enums: OrderStatus, PaymentStatus, ShipmentStatus, ToolErrorCode...
│   ├── api/
│   │   ├── whatsapp.py
│   │   ├── payment.py
│   │   ├── admin.py
│   │   └── health.py
│   ├── agent/
│   │   ├── graph.py            # build_graph()
│   │   ├── state.py            # ConversationState TypedDict
│   │   ├── nodes/
│   │   │   ├── route_intent.py
│   │   │   ├── retrieve_rag.py
│   │   │   ├── search_catalog.py
│   │   │   ├── recommend.py
│   │   │   ├── manage_cart.py
│   │   │   ├── request_confirmation.py
│   │   │   ├── create_order.py
│   │   │   ├── create_payment_link.py
│   │   │   ├── escalate.py
│   │   │   └── send_reply.py
│   │   ├── prompts/
│   │   ├── checkpointer.py     # PostgresSaver wiring
│   │   └── llm_factory.py      # LLM_Factory
│   ├── tools/
│   │   ├── catalog.py
│   │   ├── order.py
│   │   ├── payment.py
│   │   ├── logistics.py
│   │   └── support.py
│   ├── services/
│   │   ├── catalog.py
│   │   ├── order.py
│   │   ├── payment.py
│   │   ├── logistics.py
│   │   ├── conversation.py
│   │   ├── human_support.py
│   │   └── audit.py
│   ├── repositories/
│   │   ├── customers.py
│   │   ├── conversations.py
│   │   ├── messages.py
│   │   ├── products.py
│   │   ├── carts.py
│   │   ├── orders.py
│   │   ├── payments.py
│   │   ├── webhook_events.py
│   │   ├── shipments.py
│   │   ├── dispatched_actions.py
│   │   ├── audit.py
│   │   └── escalations.py
│   ├── db/
│   │   ├── base.py
│   │   ├── session.py
│   │   ├── models.py
│   │   └── migrations/         # alembic
│   ├── vectorstore/
│   │   ├── ingestion.py
│   │   └── retriever.py
│   ├── workers/
│   │   ├── queue.py            # WorkerQueue interface (in-process asyncio default)
│   │   ├── whatsapp_sender.py
│   │   ├── logistics_worker.py
│   │   └── audit_flusher.py
│   ├── schemas/
│   │   ├── whatsapp.py
│   │   ├── payment.py
│   │   ├── tools.py
│   │   ├── errors.py
│   │   └── admin.py
│   ├── observability/
│   │   ├── logging.py
│   │   ├── tracing.py
│   │   ├── metrics.py
│   │   └── audit_logger.py
│   └── utils/
│       ├── phone.py
│       ├── confirmation_token.py
│       ├── idempotency.py
│       ├── retry.py
│       └── redaction.py
├── mcp_servers/                # placeholder for future extraction
├── tests/
├── scripts/
│   └── migrate_and_seed.py
├── docs/
├── infra/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── uv.lock
├── .env.example
├── .gitignore
└── README.md
```

---

## Components and Interfaces

### API Layer (`app/api`)

#### `whatsapp.py`

- `POST /internal/whatsapp/inbound`: max body 1 MB. Bearer-token authenticated with `WHATSAPP_GATEWAY_INTERNAL_TOKEN` (constant-time comparison). On missing/invalid token → 401, no DB writes. Order of operations:
  1. Verify `Authorization: Bearer <WHATSAPP_GATEWAY_INTERNAL_TOKEN>` header (constant-time compare). On fail → 401, audit (`auth_missing` | `auth_invalid`), no DB writes.
  2. Parse body to `InboundEvent` (Pydantic). On parse failure for unrecognized event, log and return 200.
  3. For each message event: normalize phone, persist `messages_inbound` (idempotent on `baileys_message_id`), enqueue agent task. For status events: update matching `messages_outbound` row.
  4. Return 200 within 5 seconds.

#### `payment.py`

- `POST /webhooks/payment`: max body 256 KB. Order of operations:
  1. Read raw bytes.
  2. `verify_payment_signature(raw_bytes, headers, PAYMENT_WEBHOOK_SECRET)`. ≤ 500 ms. On fail → 401, audit, no DB writes.
  3. Parse to `PaymentWebhookPayload`. Parse fail → 400, audit `webhook_parse_failed`, no `webhook_event_id` row, no Order/Payment mutation.
  4. Call `PaymentService.process_webhook(event, raw_payload)`.
  5. Return 200 within 1 second for already-seen `webhook_event_id`, otherwise within 5 seconds.

#### `admin.py`

- `POST /admin/conversations/{conversation_id}/resume`: requires authenticated admin (Bearer token issued for `admin_users`). On 401/403 → audit, do not modify escalation. On success: clear escalation, audit with admin id and ISO 8601 UTC timestamp.

#### `health.py`

- `GET /healthz`: liveness only, ≤ 1 s. JSON `{"status":"ok","ts":...}`. No DB calls.
- `GET /readyz`: ≤ 5 s. Runs DB ping + checkpointer reachability check (a small SELECT against the checkpoints table). 200 if both succeed; 503 otherwise with the unavailable dependency name.

### Agent Layer (`app/agent`)

#### `graph.py` — LangGraph wiring

The LangGraph state machine for a single inbound message processing turn:

```mermaid
stateDiagram-v2
    [*] --> route_intent
    route_intent --> retrieve_rag: q&a / inquiry
    route_intent --> search_catalog: explicit search / recommendation
    route_intent --> manage_cart: cart action
    route_intent --> request_confirmation: ready to checkout
    route_intent --> create_order: customer_confirmed
    route_intent --> escalate: support / refund / complaint / low confidence

    retrieve_rag --> search_catalog: needs current price/stock
    retrieve_rag --> recommend: have grounded snippets
    retrieve_rag --> escalate: 2nd consecutive RAG miss
    retrieve_rag --> send_reply: clarifying question

    search_catalog --> recommend: results found
    search_catalog --> send_reply: zero matches
    search_catalog --> escalate: 2nd consecutive tool failure

    recommend --> send_reply

    manage_cart --> send_reply: success or validation error
    manage_cart --> escalate: 2nd consecutive tool failure

    request_confirmation --> send_reply

    create_order --> create_payment_link: order created
    create_order --> send_reply: token mismatch / price changed / stock fail
    create_order --> escalate: unrecoverable

    create_payment_link --> send_reply: link delivered
    create_payment_link --> escalate: provider unavailable repeatedly

    escalate --> send_reply

    send_reply --> [*]
```

**Edges and rules**:

- The `route_intent` node uses the LLM only to classify intent (with a structured output parser). Intent values: `inquiry`, `search`, `cart_add`, `cart_update`, `cart_remove`, `checkout_request`, `customer_confirmed`, `payment_question`, `support_request`, `complaint`, `non_text`.
- Any node that calls a tool surfaces a structured `ToolError`. The `escalate` node is reached on:
  - 2 consecutive RAG misses below `RAG_SIMILARITY_THRESHOLD` (`consecutive_rag_misses >= 2`).
  - 2 consecutive Catalog tool failures (`consecutive_catalog_failures >= 2`).
  - Customer requests human, refund/cancellation, payment problem report (Req 10.1).
  - Confidence score `< ESCALATION_CONFIDENCE_THRESHOLD` (Req 10.2).
- `send_reply` does NOT call WhatsApp directly. It enqueues a `WhatsAppSendTask`, then returns. The graph never blocks on outbound network IO inside the conversation transaction.

#### `state.py` — ConversationState

Full LangGraph state shape:

```python
from typing import TypedDict, Optional, Literal, Annotated
from operator import add

class ToolErrorRecord(TypedDict):
    code: str
    message: str
    tool: str
    occurred_at: str  # ISO 8601 UTC

class ConversationState(TypedDict, total=False):
    # Identity
    conversation_id: str
    customer_id: str
    phone_e164: str

    # Message thread (LangChain BaseMessage list, kept short for prompt; full history in messages_inbound/outbound tables)
    messages: Annotated[list[dict], add]  # serialized BaseMessage dicts

    # Latest inbound
    inbound_message_id: str
    inbound_message_text: Optional[str]
    inbound_message_type: Literal["text", "image", "audio", "video", "document", "sticker", "location"]

    # Routing / classification
    intent: Optional[str]
    intent_confidence: Optional[float]

    # RAG / catalog working memory (NOT source of truth)
    rag_snippets: list[dict]
    last_search_results: list[dict]

    # Cart / Order working references (IDs only; values come from tools)
    active_cart_id: Optional[str]
    pending_confirmation_token: Optional[str]
    last_order_id: Optional[str]
    last_payment_link: Optional[str]

    # Escalation tracking
    escalation_flag: bool
    escalation_reason: Optional[str]
    consecutive_rag_misses: int
    consecutive_catalog_failures: int

    # Errors observed during this turn (for audit + retry decisions)
    tool_errors: list[ToolErrorRecord]

    # Reply to be sent
    reply_text: Optional[str]
    reply_enqueued: bool

    # Tracing
    request_id: str
    turn_started_at: str
```

#### `checkpointer.py` — PostgresSaver

- Uses LangGraph's `langgraph.checkpoint.postgres.PostgresSaver` (via async pool).
- The `thread_id` is `conversation_id` (UUID).
- `aget_tuple(config)` is called before graph invocation; `aput(...)` is called after each step.
- Failure handling (Req 2.7–2.9):
  - Load failure → set `Escalation_Flag` transactionally; only after that, write audit log; stop processing for this message; surface non-success so the inbound webhook is re-delivered.
  - Commit failure → do NOT send reply; set `Escalation_Flag`; surface non-success.

#### `llm_factory.py` — LLM_Factory contract

```python
from typing import Protocol
from langchain_core.language_models.chat_models import BaseChatModel

SUPPORTED_PROVIDERS = {"openai", "anthropic", "google"}

class LLMFactory(Protocol):
    def get_chat_model(self, *, temperature: float = 0.0, max_tokens: int | None = None) -> BaseChatModel: ...

def build_llm_factory(settings: Settings) -> LLMFactory:
    """
    Reads LLM_PROVIDER and LLM_MODEL.
    Validates required credential env-vars (OPENAI_API_KEY | ANTHROPIC_API_KEY | GOOGLE_API_KEY).
    Returns a singleton factory; raises StartupConfigError if invalid (caught by app/main.py to fail-fast).
    Agent code MUST call factory.get_chat_model(); it MUST NOT import langchain_openai / langchain_anthropic / langchain_google_genai directly.
    """
```

### Tools Layer (`app/tools`)

All tools share:

- A Pydantic `Input` model and a Pydantic `Output` model.
- A uniform `ToolResult[T]` return shape: `{"ok": True, "data": T} | {"ok": False, "error": ToolError}`.
- Audit-by-default: each tool invocation emits exactly one audit record (success or error), via `AuditService.log_tool_invocation(...)`.
- Execution timeouts enforced via `asyncio.wait_for` with the per-requirement budgets.

| Tool | Module | Service backing | Timeout | Validates |
| --- | --- | --- | --- | --- |
| `search_products` | `tools/catalog.py` | `CatalogService.search` | 2 s (RAG) / 5 s (catalog) | Req 3.3, 3.7, 3.8 |
| `add_to_cart` | `tools/catalog.py` | `CatalogService.add_to_cart` | 5 s | Req 5.2–5.5, 5.7 |
| `update_cart_item_quantity` | `tools/catalog.py` | `CatalogService.update_cart_item_quantity` | 5 s | Req 5.6–5.8 |
| `remove_from_cart` | `tools/catalog.py` | `CatalogService.remove_from_cart` | 5 s | Req 5.6–5.8 |
| `create_order` | `tools/order.py` | `OrderService.create_order` | 5 s | Req 6.1–6.9 |
| `create_payment_link` | `tools/payment.py` | `PaymentService.create_payment_link` | 10 s | Req 7.1–7.6 |
| `prepare_shipment` (worker-only, not LLM-callable) | `tools/logistics.py` | `LogisticsService.prepare_shipment` | 5 s | Req 9.2–9.5 |
| `request_human_support` | `tools/support.py` | `HumanSupportService.escalate` | 1 s | Req 10.1, 10.5 |

### Services Layer (`app/services`)

Each service is a class with explicit async methods, taking an `AsyncSession` (or session factory) and returning typed dataclasses. Services own DB transaction boundaries.

#### `CatalogService`

- `async search(query: str, *, price_min: Decimal | None, price_max: Decimal | None, category: str | None, limit: int) -> SearchResult`
  - Joins products + variants, returns only variants where both price AND stock are known (Req 3.9).
  - Uses pgvector for relevance ranking; the SQL filter on price/category is applied server-side.
- `async add_to_cart(customer_id, product_id, variant_id, quantity)` — see Req 5.3, 5.7. Transaction:
  1. `SELECT ... FOR UPDATE` on the customer's active cart (or insert one with `OPEN`).
  2. Validate `customer_not_found` / `product_not_found` / `variant_not_found` / `variant_product_mismatch` / `invalid_quantity` / `insufficient_stock` in this exact order (Req 5.3, 5.4).
  3. Upsert `cart_items` (existing item: increment, snapshot price stays unchanged from earlier add per Req 5.5; new item: snapshot current variant price).
  4. Recompute subtotal; persist `carts.subtotal`.
  5. Commit. On any failure inside the transaction → rollback, return `ToolError` (Req 5.8).
- `async update_cart_item_quantity(...)`, `async remove_from_cart(...)`: similar transaction shape.
- Cart mutations invalidate any pending `customer_confirmation_token` by changing the cart snapshot → token derivation differs, see `OrderService.create_order`.

#### `OrderService`

- `async create_order(customer_id, cart_id, customer_confirmation_token) -> CreateOrderResult` (Req 6.1–6.9).
  - Transaction:
    1. `SELECT ... FOR UPDATE` on the cart and join cart_items.
    2. Recompute the deterministic confirmation token from the snapshot (cart id + ordered list of cart_item_ids + quantities + unit_price_snapshots + currency). If mismatch → `ToolError("token_mismatch")`, no mutation (Req 6.3).
    3. Re-validate stock against `product_variants.available_stock` (Req 6.4–6.5). On insufficient → `ToolError("insufficient_stock")`.
    4. Re-validate `current_price == unit_price_snapshot` and currency match. On drift → `ToolError("price_changed")`, then UPDATE `carts.confirmation_token_invalidated_at = now()` (Req 6.6).
    5. INSERT into `orders` with status `pending_payment`, `total = SUM(qty * unit_price_snapshot) + backend_fees`, `currency`, `created_at` (Req 6.7).
    6. INSERT into `order_items` (snapshotted from cart_items).
    7. UPDATE `carts.status = 'CONVERTED'`, link `order_id`.
    8. Commit. Audit one record.
- The agent never receives a money value to pass back; it only echoes what the tool returned (Req 6.9).

#### `PaymentService`

- `async create_payment_link(order_id) -> CreatePaymentLinkResult` (Req 7.1–7.6).
  - Validates order exists and `status == pending_payment`; otherwise structured error (Req 7.3).
  - If a `Payment` with status `paid` exists OR with status `created` and `expires_at > now()` → return existing link (Req 7.4); no provider call.
  - Else: call provider with 10 s timeout. On timeout/error → `ToolError("provider_unavailable")`, no `created` row, order unchanged (Req 7.5).
  - On success: insert `payments(status='created', provider_ref, link_url, created_at, expires_at)`; return link.
- `async process_webhook(event: PaymentWebhookEvent, raw_payload: bytes) -> WebhookResult` (Req 8.1–8.12). Single transaction:
  1. `BEGIN`.
  2. `INSERT INTO payment_webhook_events(webhook_event_id, ...) ON CONFLICT (webhook_event_id) DO NOTHING RETURNING id`.
     - If no row returned → already processed (Req 8.5). Return early `200`.
  3. `SELECT * FROM payments WHERE id = :payment_id FOR UPDATE`.
     - If not found → log audit `payment_unmatched`, ROLLBACK transaction (we do not want to keep a `webhook_event_id` for an unmatched event because Req 8.10 requires only logging), respond 200 (Req 8.10).
       - **Note**: to satisfy idempotency for unmatched events without keeping a row, we record the `provider_ref` in audit, and rely on the upstream provider's own retry semantics; alternatively, we DO insert into `payment_webhook_events` with `payment_id = NULL` so retries are still idempotent. The MVP chooses the latter to be safe.
  4. Branch on `event.kind`:
     - `successful` and `payment.status == 'created'`: UPDATE payment.status='paid', orders.status='paid', persist verification metadata (provider_ref, provider_event_id, paid_at, raw_payload). Then INSERT into `dispatched_actions(order_id, action_type)` for `logistics_prepare` and `payment_confirmation_reply` (each with `ON CONFLICT DO NOTHING`). Commit.
     - `successful` and `payment.status == 'paid'`: no Order change, no enqueue (Req 8.7). Commit (the webhook_event_id is already inserted).
     - `failed`: UPDATE payment.status='failed', leave order at `pending_payment`. Commit.
     - `expired`: UPDATE payment.status='expired', leave order at `pending_payment`. Commit.
  5. After commit (only for the first-paid case where dispatched_actions inserts succeeded): enqueue `LogisticsPrepareTask(order_id)` and `PaymentConfirmationReplyTask(order_id)`.

#### `LogisticsService`

- `async prepare_shipment(order_id) -> PrepareShipmentResult` (Req 9.1–9.6).
  - Transaction:
    1. `SELECT * FROM orders WHERE id = :order_id FOR UPDATE`. Not found → `ToolError("order_not_found")`.
    2. If `order.status == 'shipment_prepared'` AND a `shipments` row exists → return existing shipment, mark `notification_already_sent=True` so the worker does NOT enqueue another notification (Req 9.5).
    3. If `order.status != 'paid'` → `ToolError("order_not_paid")` (Req 9.4).
    4. Generate a unique tracking number (8–32 alphanumeric, derived deterministically from `order_id + UUID` salt; `UNIQUE(tracking_number)` enforces uniqueness on the database).
    5. INSERT `shipments(order_id, tracking_number, status='prepared', created_at)`.
    6. UPDATE `orders.status = 'shipment_prepared'`.
    7. Commit.
  - Returns the shipment record. The worker calling this is responsible for enqueuing the WhatsApp tracking notification only when `notification_already_sent` is False (Req 9.6).

#### `ConversationService`

- `async get_or_create_by_phone(phone_raw) -> Conversation` — normalizes phone, validates E.164 (Req 2.1, 2.2). On invalid → returns structured error; the WhatsApp webhook handler then writes an audit record and does NOT create a conversation (Req 2.2).
- `async set_escalation_flag(conversation_id, reason, actor)` — transactional; persists `escalations` row + sets `conversations.escalation_flag = TRUE` in one transaction.
- `async clear_escalation_flag(conversation_id, admin_user_id)` — used by admin endpoint.
- `async persist_escalation_context(conversation_id)` — snapshots last 50 messages + cart/order references (Req 10.5). Retry up to 3 times with ≥1 s backoff (Req 10.6).

#### `HumanSupportService`

- `async escalate(conversation_id, reason)` — wraps `ConversationService.set_escalation_flag` + queues persistence of escalation context.

#### `AuditService` and `AuditLogger`

- The synchronous code path (webhook handler / tool) calls `AuditLogger.emit(record)`; this is non-blocking (drop into in-memory ring buffer, ≤100 ms blocking budget, Req 11.6).
- A dedicated `audit_flusher` worker batches and writes to `audit_logs`. On write failure, retry up to 3 times with ≥1 s backoff; permanent failures go to `audit_log_failures` (durable table) for later replay (Req 11.7).
- `AuditLogger.emit` runs `redact()` on inputs/outputs to scrub credentials, API keys, full PANs (Req 11.8).

### Repositories Layer (`app/repositories`)

Repositories expose narrow async functions, accept either an `AsyncSession` or a `Connection`, and never own transactions (services do). They are responsible only for SQL.

Examples:

- `customers.py`: `get_by_phone(phone_e164)`, `upsert(phone_e164)`.
- `messages.py`: `insert_inbound_idempotent(whatsapp_message_id, ...)`, `insert_outbound(...)`, `update_outbound_status(...)`.
- `webhook_events.py`: `try_insert(webhook_event_id, payment_id, raw_payload, signature_header) -> bool` (returns False on conflict).
- `dispatched_actions.py`: `try_dispatch(order_id, action_type) -> bool` (returns False on conflict).

### Vectorstore Layer (`app/vectorstore`)

- `ingestion.py`:
  - On product/FAQ row insert/update, schedule a background task that computes embeddings and upserts into `product_embeddings` / `faq_embeddings` (single embedding per chunk).
  - Within 5 minutes of the source row change (Req 3.2). For MVP, use a Postgres `LISTEN/NOTIFY` channel or a periodic 60-second polling sweep.
- `retriever.py`:
  - Wraps a LangChain `VectorStoreRetriever` over pgvector with HNSW or IVFFlat index.
  - Returns up to `RAG_TOP_K` (default 5) items with similarity ≥ `RAG_SIMILARITY_THRESHOLD` (default 0.7); ≤ 2 s budget (Req 3.3).

### Workers Layer (`app/workers`)

#### `queue.py` — Worker queue interface

```python
class WorkerQueue(Protocol):
    async def enqueue(self, task_type: str, payload: dict, *, dedupe_key: str | None = None) -> None: ...
```

- Default: `InProcessAsyncQueue` backed by `asyncio.Queue`, with at-least-once semantics and a DB-backed `dispatched_actions` unique constraint for cross-process dedup.
- Swappable: `CloudTasksQueue` for horizontal scaling. The agent and services depend only on `WorkerQueue`, never on a specific implementation.

#### Workers

- `whatsapp_sender.py`: consumes `WhatsAppSendTask`. Per attempt timeout 10 s, retry 1 s → 2 s → 4 s → cap 8 s, up to 3 attempts total (Req 1.9). Persists `messages_outbound` row with `sent` or `failed` status; logs failures to audit.
- `logistics_worker.py`: consumes `LogisticsPrepareTask(order_id)`. Calls `LogisticsService.prepare_shipment(order_id)`. On success and `notification_already_sent == False`, enqueues a `WhatsAppSendTask` containing the tracking number. On `order_not_paid` (race), no retry; on transient DB error, retry with backoff.
- `audit_flusher.py`: consumes `AuditEvent` from in-memory queue, batches into INSERTs, retries on failure, drains to `audit_log_failures` on permanent failure (Req 11.7).

### Observability Layer (`app/observability`)

- `logging.py`: structlog or stdlib JSON logging. Every log line includes `request_id`, `conversation_id` (when known), `customer_id` (when known), `tool` (when applicable), `order_id` (when applicable).
- `tracing.py`: OpenTelemetry hooks for FastAPI, asyncpg, and httpx; exports to OTLP if configured.
- `metrics.py`: counters/histograms for webhook latency, agent turn duration, tool error rate, escalation count, retry count.
- `audit_logger.py`: described above.

### Utilities Layer (`app/utils`)

- `phone.py`: `normalize_to_e164(raw: str) -> str | None`. Idempotent. Uses `phonenumbers`. Returns `None` on invalid (Req 2.2).
- `signature.py`:
  - `verify_whatsapp_signature(raw_body: bytes, header: str | None, secret: str) -> SignatureCheck`
  - `verify_payment_signature(raw_body: bytes, header: str | None, secret: str) -> SignatureCheck`
  - Constant-time comparison; returns `SignatureCheck.OK | MISSING | MALFORMED | MISMATCH`.
- `confirmation_token.py`: `derive_token(snapshot: CartSnapshot) -> str`. Deterministic SHA-256 over a canonical serialization of `(cart_id, [(item_id, quantity, unit_price_snapshot)], currency)` sorted by `item_id`.
- `idempotency.py`: helpers around `INSERT ... ON CONFLICT DO NOTHING`.
- `retry.py`: exponential backoff with cap.
- `redaction.py`: regex-based scrubber for `Authorization`, `*_API_KEY`, `account_number`, etc.

---

## Sequence Diagrams

### Happy-path checkout (inbound message → agent → cart → confirm → order → payment link → outbound reply)

```mermaid
sequenceDiagram
    autonumber
    participant WA_GW as WhatsApp Gateway (Baileys)
    participant API as FastAPI /internal/whatsapp/inbound
    participant CONV as ConversationService
    participant Q as WorkerQueue
    participant AGENT as LangGraph Agent
    participant TOOL as Tools
    participant CAT as CatalogService
    participant ORD as OrderService
    participant PAY as PaymentService
    participant DB as PostgreSQL
    participant W as WhatsApp Sender Worker

    WA_GW->>API: POST /internal/whatsapp/inbound (Bearer token)
    API->>API: verify Bearer token (WHATSAPP_GATEWAY_INTERNAL_TOKEN, constant-time)
    API->>CONV: get_or_create_by_phone(E.164)
    CONV->>DB: SELECT/INSERT customer + conversation
    API->>DB: INSERT messages_inbound (idempotent on baileys_message_id)
    API->>Q: enqueue AgentTurnTask(conversation_id, message_id)
    API-->>WA_GW: 200 OK (≤5s)

    Q->>AGENT: invoke graph
    AGENT->>DB: PostgresSaver.aget_tuple(thread=conversation_id)
    AGENT->>AGENT: route_intent → manage_cart
    AGENT->>TOOL: add_to_cart(...)
    TOOL->>CAT: add_to_cart(...)
    CAT->>DB: BEGIN; FOR UPDATE cart; validate; upsert cart_items; recompute subtotal; COMMIT
    CAT-->>TOOL: ToolResult.ok(cart snapshot)
    TOOL-->>AGENT: cart snapshot

    AGENT->>AGENT: request_confirmation (LLM presents totals)
    AGENT->>DB: PostgresSaver.aput(state)
    AGENT->>Q: enqueue WhatsAppSendTask(reply)
    W->>WA_GW: POST /send (Bearer token, ≤10s, retry 3x)

    Note over WA_GW,API: Customer replies "ya, lanjutkan"

    WA_GW->>API: POST /internal/whatsapp/inbound (Bearer token)
    API->>API: verify Bearer token + persist + enqueue
    AGENT->>AGENT: route_intent → create_order
    AGENT->>TOOL: create_order(customer_id, cart_id, token)
    TOOL->>ORD: create_order(...)
    ORD->>DB: BEGIN; FOR UPDATE cart; recompute token; revalidate stock+price; INSERT orders/order_items; UPDATE cart=CONVERTED; COMMIT
    ORD-->>TOOL: ok(order_id, total, currency)
    AGENT->>TOOL: create_payment_link(order_id)
    TOOL->>PAY: create_payment_link(order_id)
    PAY->>PAY: provider call (≤10s)
    PAY->>DB: INSERT payments(status=created, link, expires_at)
    PAY-->>TOOL: ok(link, expires_at)
    AGENT->>DB: PostgresSaver.aput(state)
    AGENT->>Q: enqueue WhatsAppSendTask(link)
    W->>WA_GW: POST /send with link (Bearer token)
```

### Payment webhook → order paid → logistics preparation → tracking notification

```mermaid
sequenceDiagram
    autonumber
    participant PP as Payment Provider
    participant API as FastAPI /webhooks/payment
    participant PAY as PaymentService
    participant DB as PostgreSQL
    participant Q as WorkerQueue
    participant LW as LogisticsWorker
    participant LOG as LogisticsService
    participant W as WhatsApp Sender Worker
    participant WA_GW as WhatsApp Gateway (Baileys)

    PP->>API: POST /webhooks/payment (raw bytes + signature)
    API->>API: verify HMAC (PAYMENT_WEBHOOK_SECRET, ≤500ms)
    API->>PAY: process_webhook(event, raw_payload)

    PAY->>DB: BEGIN
    PAY->>DB: INSERT payment_webhook_events ON CONFLICT DO NOTHING
    alt webhook_event_id already exists
        PAY->>DB: COMMIT (no-op state change)
        PAY-->>API: 200 OK (≤1s)
    else first time
        PAY->>DB: SELECT payments FOR UPDATE
        alt event=paid AND payment.status=created
            PAY->>DB: UPDATE payments.status=paid, orders.status=paid, persist verification
            PAY->>DB: INSERT dispatched_actions(order_id, 'logistics_prepare') ON CONFLICT DO NOTHING
            PAY->>DB: INSERT dispatched_actions(order_id, 'payment_confirmation_reply') ON CONFLICT DO NOTHING
            PAY->>DB: COMMIT
            PAY->>Q: enqueue LogisticsPrepareTask(order_id)
            PAY->>Q: enqueue WhatsAppSendTask(payment confirmation)
        else event=paid AND payment.status=paid
            PAY->>DB: COMMIT
        else event=failed/expired
            PAY->>DB: UPDATE payments.status; COMMIT
        end
        PAY-->>API: 200 OK (≤5s)
    end
    API-->>PP: 200 OK

    Q->>LW: LogisticsPrepareTask(order_id)
    LW->>LOG: prepare_shipment(order_id)
    LOG->>DB: BEGIN; FOR UPDATE orders
    alt status=paid (no shipment yet)
        LOG->>DB: INSERT shipments(tracking_number); UPDATE orders.status=shipment_prepared; COMMIT
        LOG-->>LW: shipment(notification_already_sent=False)
        LW->>Q: enqueue WhatsAppSendTask(tracking notification)
    else shipment already exists
        LOG-->>LW: shipment(notification_already_sent=True)
    end
    Q->>W: WhatsAppSendTask
    W->>WA_GW: POST /send (Bearer token, ≤30s end-to-end)
```

---

## Data Models

All tables live in PostgreSQL. The pgvector extension is enabled. Timestamps are `TIMESTAMPTZ` and stored in UTC. Money values use `NUMERIC(18, 4)` and ISO 4217 currency codes (`CHAR(3)`). IDs are `UUID` (`uuid_generate_v4()`), except for external provider IDs which are `TEXT`.

### `customers`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `phone_e164` | TEXT NOT NULL UNIQUE | Idempotency anchor; Req 2.1. |
| `display_name` | TEXT NULL | Optional, from WA profile. |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |

Indexes: `UNIQUE(phone_e164)`.

### `conversations`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | Used as LangGraph `thread_id`. |
| `customer_id` | UUID NOT NULL FK→customers(id) | |
| `escalation_flag` | BOOLEAN NOT NULL DEFAULT FALSE | Req 10. |
| `escalation_reason` | TEXT NULL | |
| `last_message_at` | TIMESTAMPTZ NULL | |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |

Indexes: `UNIQUE(customer_id)` (one active conversation thread per customer for MVP), `INDEX(escalation_flag)`.

### `conversation_checkpoints`

Owned by LangGraph's `PostgresSaver`. We do not modify this schema directly; we only ensure migrations create it on startup. Keys:

- `thread_id` = `conversations.id`.
- `checkpoint_id` = LangGraph commit timestamp/uuid.

### `messages_inbound`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `conversation_id` | UUID FK→conversations(id) NULL | Nullable because Req 2.2 rejects messages with invalid phone. |
| `baileys_message_id` | TEXT NOT NULL | Idempotency key (Baileys `key.id`). |
| `from_phone_raw` | TEXT NOT NULL | Pre-normalization. |
| `from_phone_e164` | TEXT NULL | Post-normalization (NULL on invalid). |
| `message_type` | TEXT NOT NULL | text/image/audio/video/document/sticker/location. |
| `text_body` | TEXT NULL | |
| `event_timestamp` | TIMESTAMPTZ NOT NULL | From Baileys payload. |
| `received_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `raw_payload` | JSONB NOT NULL | |

Indexes: `UNIQUE(baileys_message_id)`, `INDEX(conversation_id, received_at)`.

### `messages_outbound`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `conversation_id` | UUID NOT NULL FK | |
| `to_phone_e164` | TEXT NOT NULL | |
| `whatsapp_message_id` | TEXT NULL UNIQUE | Populated on first send response. |
| `text_body` | TEXT NOT NULL | |
| `status` | TEXT NOT NULL CHECK in (`queued`,`sent`,`failed`,`delivered`,`read`) | |
| `attempts` | INT NOT NULL DEFAULT 0 | |
| `last_error` | TEXT NULL | |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `sent_at` | TIMESTAMPTZ NULL | |
| `last_status_at` | TIMESTAMPTZ NULL | |

Indexes: `INDEX(conversation_id, created_at DESC)`, `UNIQUE(whatsapp_message_id)` (when not null).

### `products`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `sku` | TEXT NOT NULL UNIQUE | |
| `name` | TEXT NOT NULL | |
| `description` | TEXT NOT NULL | |
| `category` | TEXT NULL | |
| `is_active` | BOOLEAN NOT NULL DEFAULT TRUE | |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

### `product_variants`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `product_id` | UUID NOT NULL FK | |
| `variant_sku` | TEXT NOT NULL UNIQUE | |
| `attributes` | JSONB NOT NULL | e.g. `{"color":"black","size":"M"}`. |
| `current_price` | NUMERIC(18,4) NOT NULL CHECK >= 0 | |
| `currency` | CHAR(3) NOT NULL | ISO 4217. |
| `available_stock` | INT NOT NULL CHECK >= 0 | |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

Indexes: `INDEX(product_id)`, `INDEX(current_price)`, `INDEX(available_stock)`.

### `product_embeddings`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `product_id` | UUID NOT NULL FK | |
| `chunk_index` | INT NOT NULL | |
| `chunk_text` | TEXT NOT NULL | |
| `embedding` | VECTOR(d) | `d` matches the configured embedding model. |
| `updated_at` | TIMESTAMPTZ NOT NULL | |

Indexes: pgvector index (HNSW or IVFFlat), `UNIQUE(product_id, chunk_index)`.

### `faq_documents`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `title` | TEXT NOT NULL | |
| `body` | TEXT NOT NULL | |
| `is_active` | BOOLEAN NOT NULL DEFAULT TRUE | |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

### `faq_embeddings`

Same shape as `product_embeddings` but referencing `faq_documents`.

### `carts`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `customer_id` | UUID NOT NULL FK | |
| `status` | TEXT NOT NULL CHECK in (`OPEN`,`CONVERTED`,`EXPIRED`,`ABANDONED`) | |
| `subtotal` | NUMERIC(18,4) NOT NULL DEFAULT 0 | |
| `currency` | CHAR(3) NOT NULL | |
| `confirmation_token_invalidated_at` | TIMESTAMPTZ NULL | Set on price drift (Req 6.6). |
| `order_id` | UUID NULL FK→orders(id) | Set on conversion. |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

Indexes: `UNIQUE(customer_id) WHERE status = 'OPEN'` (partial index → at most one active cart per customer; Req 5.1).

### `cart_items`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `cart_id` | UUID NOT NULL FK | |
| `product_id` | UUID NOT NULL FK | |
| `variant_id` | UUID NOT NULL FK | |
| `quantity` | INT NOT NULL CHECK BETWEEN 1 AND 999 | |
| `unit_price_snapshot` | NUMERIC(18,4) NOT NULL CHECK >= 0 | Req 5.5. |
| `currency` | CHAR(3) NOT NULL | |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

Indexes: `UNIQUE(cart_id, variant_id)`, `INDEX(cart_id)`.

### `orders`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `customer_id` | UUID NOT NULL FK | |
| `cart_id` | UUID NOT NULL FK | |
| `status` | TEXT NOT NULL CHECK in (`pending_payment`,`paid`,`shipment_prepared`,`cancelled`) | |
| `total` | NUMERIC(18,4) NOT NULL | Req 6.7. |
| `currency` | CHAR(3) NOT NULL | |
| `backend_fees` | NUMERIC(18,4) NOT NULL DEFAULT 0 | |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `paid_at` | TIMESTAMPTZ NULL | |
| `shipment_prepared_at` | TIMESTAMPTZ NULL | |

Indexes: `UNIQUE(cart_id)`, `INDEX(customer_id, created_at DESC)`, `INDEX(status)`.

### `order_items`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `order_id` | UUID NOT NULL FK | |
| `product_id` | UUID NOT NULL FK | |
| `variant_id` | UUID NOT NULL FK | |
| `quantity` | INT NOT NULL | |
| `unit_price_snapshot` | NUMERIC(18,4) NOT NULL | |
| `currency` | CHAR(3) NOT NULL | |
| `line_total` | NUMERIC(18,4) NOT NULL | |

Indexes: `INDEX(order_id)`.

### `payments`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `order_id` | UUID NOT NULL FK | |
| `provider` | TEXT NOT NULL | matches `PAYMENT_PROVIDER`. |
| `provider_ref` | TEXT NOT NULL UNIQUE | provider's payment id. |
| `link_url` | TEXT NOT NULL | |
| `status` | TEXT NOT NULL CHECK in (`created`,`paid`,`failed`,`expired`) | |
| `amount` | NUMERIC(18,4) NOT NULL | |
| `currency` | CHAR(3) NOT NULL | |
| `expires_at` | TIMESTAMPTZ NOT NULL | |
| `paid_at` | TIMESTAMPTZ NULL | |
| `verification_metadata` | JSONB NULL | provider_ref, provider_event_id, raw_payload (Req 8.6). |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

Indexes: `INDEX(order_id)`, `UNIQUE(provider, provider_ref)`.

### `payment_webhook_events`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `webhook_event_id` | TEXT NOT NULL UNIQUE | Idempotency key (Req 8.4). |
| `payment_id` | UUID NULL FK→payments(id) | NULL for unmatched (Req 8.10). |
| `event_kind` | TEXT NOT NULL CHECK in (`paid`,`failed`,`expired`,`unmatched`) | |
| `signature_header` | TEXT NULL | |
| `raw_payload` | JSONB NOT NULL | |
| `received_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `processed_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |

Indexes: `UNIQUE(webhook_event_id)`, `INDEX(payment_id)`.

### `shipments`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `order_id` | UUID NOT NULL UNIQUE FK | At most one shipment per order. |
| `tracking_number` | TEXT NOT NULL UNIQUE CHECK length BETWEEN 8 AND 32 AND alphanumeric | Req 9.2. |
| `status` | TEXT NOT NULL CHECK in (`prepared`,`in_transit`,`delivered`,`failed`) | |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |

### `dispatched_actions`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `order_id` | UUID NOT NULL FK | |
| `action_type` | TEXT NOT NULL CHECK in (`logistics_prepare`,`payment_confirmation_reply`,`tracking_notification`) | |
| `dispatched_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |

Indexes: `UNIQUE(order_id, action_type)` — the dedup primitive for Req 8.12 and 9.6.

### `audit_logs`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `actor` | TEXT NOT NULL | `agent` / `system` / `admin:<id>` / `webhook:whatsapp` / `webhook:payment`. |
| `event_type` | TEXT NOT NULL | tool_invocation / signature_failure / escalation_set / escalation_cleared / webhook_unmatched / webhook_parse_failed / outbound_send_result. |
| `tool_name` | TEXT NULL | |
| `conversation_id` | UUID NULL | |
| `customer_id` | UUID NULL | |
| `order_id` | UUID NULL | |
| `request_id` | TEXT NULL | |
| `dedupe_key` | TEXT NULL UNIQUE | tool invocation id or webhook event id (Req 11.6). |
| `input_redacted` | JSONB NULL | After redaction (Req 11.8). |
| `output_redacted` | JSONB NULL | |
| `error_code` | TEXT NULL | |
| `occurred_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |

Indexes: `UNIQUE(dedupe_key) WHERE dedupe_key IS NOT NULL`, `INDEX(occurred_at)`, `INDEX(conversation_id)`, `INDEX(order_id)`.

### `audit_log_failures`

Mirror of `audit_logs` plus `attempt_count`, `last_error`, `last_attempt_at`. Used as the durable failure queue for Req 11.7.

### `escalations`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `conversation_id` | UUID NOT NULL FK | |
| `reason` | TEXT NOT NULL | |
| `actor` | TEXT NOT NULL | `agent` or `admin:<id>`. |
| `context_snapshot` | JSONB NULL | last 50 messages + cart/order refs (Req 10.5). |
| `is_active` | BOOLEAN NOT NULL DEFAULT TRUE | |
| `set_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `cleared_at` | TIMESTAMPTZ NULL | |

Indexes: `INDEX(conversation_id, is_active)`.

### `admin_users`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `email` | TEXT NOT NULL UNIQUE | |
| `password_hash` | TEXT NOT NULL | bcrypt/argon2. |
| `is_active` | BOOLEAN NOT NULL DEFAULT TRUE | |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

### Schema Diagram (key relationships)

```mermaid
erDiagram
    customers ||--o{ conversations : has
    conversations ||--o{ messages_inbound : receives
    conversations ||--o{ messages_outbound : sends
    customers ||--o{ carts : owns
    carts ||--o{ cart_items : contains
    cart_items }o--|| product_variants : references
    product_variants }o--|| products : variant_of
    products ||--o{ product_embeddings : indexed_by
    customers ||--o{ orders : places
    carts ||--|| orders : converted_into
    orders ||--o{ order_items : contains
    orders ||--o{ payments : has
    payments ||--o{ payment_webhook_events : received
    orders ||--o| shipments : ships
    orders ||--o{ dispatched_actions : dedup_per_action
    conversations ||--o{ escalations : flagged
```

### State Machines

#### Order status

```mermaid
stateDiagram-v2
    [*] --> pending_payment: create_order (Req 6.8)
    pending_payment --> paid: verified payment_webhook=paid (Req 8.6)
    paid --> shipment_prepared: prepare_shipment (Req 9.2)
    pending_payment --> cancelled: admin action (out of MVP scope, schema only)
    paid --> [*]
    shipment_prepared --> [*]
```

Legal transitions for Order:

- `pending_payment` → `paid` (Req 8.6, only via verified webhook with matching `webhook_event_id` not yet seen)
- `paid` → `shipment_prepared` (Req 9.2, only via `LogisticsService.prepare_shipment`)
- All other transitions are illegal and rejected by the service with a structured error.

#### Payment status

```mermaid
stateDiagram-v2
    [*] --> created: create_payment_link (Req 7.2)
    created --> paid: webhook=successful (Req 8.6)
    created --> failed: webhook=failed (Req 8.8)
    created --> expired: webhook=expired (Req 8.9)
    paid --> paid: duplicate webhook=successful (Req 8.7, no-op)
```

Legal transitions for Payment:

- `created` → `paid` (Req 8.6)
- `created` → `failed` (Req 8.8)
- `created` → `expired` (Req 8.9)
- `paid`, `failed`, `expired` are terminal in the MVP.

#### Cart status

```mermaid
stateDiagram-v2
    [*] --> OPEN: first cart-mutating tool call (Req 5.10)
    OPEN --> CONVERTED: create_order success (Req 6.8)
    OPEN --> EXPIRED: TTL elapsed (out of MVP)
    OPEN --> ABANDONED: admin action (out of MVP)
    CONVERTED --> [*]
```

Legal transitions for Cart:

- `OPEN` → `CONVERTED` is the only transition exercised by the MVP. The partial unique index `UNIQUE(customer_id) WHERE status='OPEN'` enforces Req 5.1.

#### Shipment status

`prepared` is the only status the MVP writes (`in_transit`, `delivered`, `failed` are reserved for later).

### Idempotency Keys

| Key | Where it lives | Purpose | Requirement |
| --- | --- | --- | --- |
| `baileys_message_id` | `messages_inbound.baileys_message_id UNIQUE` | Inbound dedup | 1.6, 1.8 |
| `webhook_event_id` | `payment_webhook_events.webhook_event_id UNIQUE` | Payment webhook dedup | 8.4, 8.5, 8.7 |
| `(provider, provider_ref)` | `payments` UNIQUE | One Payment per provider id | 7, 8 |
| `customer_confirmation_token` | derived in `OrderService` | Prevents stale-cart order creation | 6.3, 6.6 |
| `(order_id, action_type)` | `dispatched_actions` UNIQUE | One logistics + one notification per order | 8.12, 9.5–9.6 |
| `audit_logs.dedupe_key` | `tool invocation id` or `webhook_event_id` | Audit exactly-once | 11.6 |
| `tracking_number` | `shipments.tracking_number UNIQUE` | One tracking number per shipment | 9.2 |
| `payments.expires_at + status='created'` | logical key | Reuse-on-existing for `create_payment_link` | 7.4 |

---

## Webhook Handling Subsystem

### WhatsApp Inbound Channel (`/internal/whatsapp/inbound`)

**POST (inbound events from the WhatsApp Gateway)**

```python
@router.post("/internal/whatsapp/inbound")
async def receive(request: Request, audit: AuditLogger = Depends(...), conv: ConversationService = Depends(...), queue: WorkerQueue = Depends(...)):
    raw = await request.body()                 # bytes
    if len(raw) > 1 * 1024 * 1024:             # body size cap 1 MB
        return PlainTextResponse("payload too large", status_code=413)

    # Bearer-token auth (constant-time comparison)
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or not safe_compare(auth_header[7:], settings.WHATSAPP_GATEWAY_INTERNAL_TOKEN.get_secret_value()):
        await audit.auth_failure(source="whatsapp_inbound", reason="invalid_bearer", ip=request.client.host)
        return PlainTextResponse("unauthorized", status_code=401)

    payload = InboundEvent.model_validate_json(raw)        # Pydantic
    if payload.is_status_update():                         # Req 1.11 (status propagation)
        await messages_repo.update_outbound_status(payload.baileys_message_id, payload.status, payload.status_at)
        return Response(status_code=200)
    # Inbound message
    e164 = normalize_to_e164(payload.from_phone)
    if e164 is None:
        await audit.invalid_phone(raw_phone=payload.from_phone, message_id=payload.baileys_message_id)  # Req 2.2
        return Response(status_code=200)
    async with db.transaction():
        inserted = await messages_repo.insert_inbound_idempotent(payload)   # Req 1.6 (UNIQUE(baileys_message_id))
        if not inserted:
            return Response(status_code=200)                                 # duplicate
        customer = await conv.upsert_customer(e164)
        conversation = await conv.get_or_create(customer.id)                # Req 2.3
    if payload.message_type != "text":                                       # Req 1.11
        await queue.enqueue("non_text_reply", {"conversation_id": str(conversation.id), "message_id": payload.baileys_message_id})
        return Response(status_code=200)
    await queue.enqueue("agent_turn", {"conversation_id": str(conversation.id), "message_id": payload.baileys_message_id},
                        dedupe_key=f"agent:{payload.baileys_message_id}")   # Req 1.6
    return Response(status_code=200)
```

**Outbound send semantics**

```python
async def send_text(to_phone_e164: str, body: str, idempotency_key: str) -> WhatsAppSendResult:
    # 4096-character chunking policy: split on whitespace boundaries, then enforce hard cap.
    chunks = chunk_for_whatsapp(body, max_len=4096)
    last_error: str | None = None
    for chunk in chunks:
        for attempt, backoff in enumerate(EXPONENTIAL_BACKOFF, start=1):  # 1s, 2s, 4s capped at 8s, 3 attempts
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{settings.WHATSAPP_GATEWAY_URL}/send",
                        json={"to": to_phone_e164, "body": chunk, "idempotency_key": idempotency_key},
                        headers={"Authorization": f"Bearer {settings.WHATSAPP_GATEWAY_INTERNAL_TOKEN.get_secret_value()}"}
                    )
                if resp.is_success:
                    await messages_repo.record_sent(...)
                    break
                last_error = f"http_{resp.status_code}"
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_error = e.__class__.__name__
            await asyncio.sleep(backoff)
        else:
            await audit.outbound_send_failure(...)                        # Req 1.9
            await messages_repo.record_failed(last_error=last_error)
            return WhatsAppSendResult(ok=False, last_error=last_error)
    return WhatsAppSendResult(ok=True)
```

Constants: `EXPONENTIAL_BACKOFF = (1.0, 2.0, 4.0)` (capped at 8s by design), `max_attempts = 3` (Req 1.9). The 8s cap means a future fourth attempt would still be 8s; in MVP we stop at 3.

### Payment Webhook (`/webhooks/payment`)

```python
@router.post("/webhooks/payment")
async def receive(request: Request, audit: AuditLogger = Depends(...), payment: PaymentService = Depends(...)):
    raw = await request.body()
    if len(raw) > 256 * 1024:                                          # Req 8.1
        return JSONResponse({"error": "payload_too_large"}, status_code=413)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":  # Req 8.1
        return JSONResponse({"error": "unsupported_media_type"}, status_code=415)

    started = monotonic()
    sig = verify_payment_signature(raw, request.headers.get("X-Payment-Signature"), settings.PAYMENT_WEBHOOK_SECRET)
    elapsed_ms = (monotonic() - started) * 1000
    assert elapsed_ms <= 500, "verifier exceeded 500ms"                # Req 8.2 budget; tested
    if not sig.ok:
        await audit.signature_failure(source="payment", reason=sig.reason, ip=request.client.host)
        return JSONResponse({"error": "unauthorized"}, status_code=401)  # Req 8.3

    try:
        event = PaymentWebhookPayload.model_validate_json(raw)         # Req 8.11
    except ValidationError as e:
        await audit.parse_failure(source="payment", error=str(e))
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    result = await payment.process_webhook(event, raw)
    return JSONResponse(result.body, status_code=result.status)
```

`PaymentService.process_webhook(...)` returns `(status, body)`:

- already-seen → `(200, {"status":"duplicate"})` within 1 s
- newly processed → `(200, {"status":"ok"})` within 5 s
- unmatched provider_ref → `(200, {"status":"unmatched"})` within 1 s

---

## Service Layer Design (Function Signatures)

```python
# app/services/catalog.py
class CatalogService:
    async def search(self, query: str, *, price_min: Decimal | None = None, price_max: Decimal | None = None,
                     category: str | None = None, limit: int = 20) -> SearchResult: ...
    async def get_variant_with_price_and_stock(self, variant_id: UUID) -> VariantSnapshot | None: ...
    async def add_to_cart(self, customer_id: UUID, product_id: UUID, variant_id: UUID, quantity: int) -> ToolResult[CartView]: ...
    async def update_cart_item_quantity(self, customer_id: UUID, cart_item_id: UUID, quantity: int) -> ToolResult[CartView]: ...
    async def remove_from_cart(self, customer_id: UUID, cart_item_id: UUID) -> ToolResult[CartView]: ...
    async def get_cart(self, customer_id: UUID) -> CartView | None: ...

# app/services/order.py
class OrderService:
    async def create_order(self, customer_id: UUID, cart_id: UUID, customer_confirmation_token: str) -> ToolResult[OrderView]: ...

# app/services/payment.py
class PaymentService:
    async def create_payment_link(self, order_id: UUID) -> ToolResult[PaymentLinkView]: ...
    async def process_webhook(self, event: PaymentWebhookPayload, raw_payload: bytes) -> WebhookProcessResult: ...

# app/services/logistics.py
class LogisticsService:
    async def prepare_shipment(self, order_id: UUID) -> ToolResult[ShipmentView]: ...

# app/services/conversation.py
class ConversationService:
    async def upsert_customer(self, phone_e164: str) -> Customer: ...
    async def get_or_create(self, customer_id: UUID) -> Conversation: ...
    async def set_escalation_flag(self, conversation_id: UUID, *, reason: str, actor: str) -> None: ...
    async def clear_escalation_flag(self, conversation_id: UUID, *, admin_user_id: UUID) -> None: ...
    async def persist_escalation_context(self, conversation_id: UUID) -> None: ...

# app/services/human_support.py
class HumanSupportService:
    async def escalate(self, conversation_id: UUID, *, reason: str, actor: str = "agent") -> None: ...

# app/services/audit.py
class AuditService:
    async def log_tool_invocation(self, *, dedupe_key: str, tool: str, input_: dict, output: dict | None,
                                  error_code: str | None, conversation_id: UUID | None, customer_id: UUID | None,
                                  order_id: UUID | None) -> None: ...
```

### Tool Function Signatures

```python
# app/tools/catalog.py
@tool("search_products", args_schema=SearchProductsInput)
async def search_products(query: str, price_min: float | None = None, price_max: float | None = None,
                          category: str | None = None) -> dict: ...

@tool("add_to_cart", args_schema=AddToCartInput)
async def add_to_cart(customer_id: str, product_id: str, variant_id: str, quantity: int) -> dict: ...

@tool("update_cart_item_quantity", args_schema=UpdateCartItemQuantityInput)
async def update_cart_item_quantity(customer_id: str, cart_item_id: str, quantity: int) -> dict: ...

@tool("remove_from_cart", args_schema=RemoveFromCartInput)
async def remove_from_cart(customer_id: str, cart_item_id: str) -> dict: ...

@tool("get_cart", args_schema=GetCartInput)
async def get_cart(customer_id: str) -> dict: ...

# app/tools/order.py
@tool("create_order", args_schema=CreateOrderInput)
async def create_order(customer_id: str, cart_id: str, customer_confirmation_token: str) -> dict: ...

# app/tools/payment.py
@tool("create_payment_link", args_schema=CreatePaymentLinkInput)
async def create_payment_link(order_id: str) -> dict: ...

# app/tools/support.py
@tool("escalate_to_human", args_schema=EscalateInput)
async def escalate_to_human(conversation_id: str, reason: str) -> dict: ...
```

`prepare_shipment` is intentionally NOT a LangChain tool surface; it is invoked by the logistics worker only. This eliminates a class of LLM-driven safety bugs (Req 9 prereq: shipment preparation must be system-driven, not LLM-driven).

---

## Worker / Async Processing

### MVP choice: in-process asyncio + DB-backed dedup

For the MVP we run a single Cloud Run container that hosts both the FastAPI app and an in-process async worker pool started via FastAPI's lifespan hook. Tasks are kept on:

1. An in-process `asyncio.Queue` for low-latency dispatch.
2. A DB-backed `dispatched_actions` table for at-least-once and cross-restart durability where the requirement demands single-dispatch semantics (logistics, payment confirmation).

**Why this is acceptable for MVP**

- WhatsApp inbound traffic for a small/medium seller is on the order of 1–10 messages/second peak; a single Cloud Run instance with `min_instances=1` handles this easily.
- The non-negotiable correctness invariants (idempotent payment processing, single logistics dispatch, single notification) are enforced by **DB unique constraints**, not the queue. This means even if the queue is replaced, semantics are preserved.

**Trade-off vs Cloud Tasks**

- Cloud Tasks gives durable per-task retries, observable backlog, and decoupled scaling. The MVP defers this complexity.
- Risk: a container crash between commit and `queue.enqueue` could miss a logistics dispatch. Mitigation: a periodic reconciler (`workers/reconciler.py`) scans for `orders.status='paid' AND NOT EXISTS dispatched_actions(action='logistics_prepare')` every 60s and re-enqueues.

**Migration path to Cloud Tasks**

- The `WorkerQueue` interface is the only abstraction services depend on. A `CloudTasksQueue` implementation is a drop-in replacement with the same `enqueue(task_type, payload, dedupe_key)` contract.
- The worker handlers themselves move from `asyncio.Task` consumers to FastAPI endpoints (`POST /internal/tasks/{task_type}`) protected by Cloud Tasks OIDC. No service-layer change.

### Deduplication keys at enqueue time

| Task | Dedupe key |
| --- | --- |
| `agent_turn` | `agent:{baileys_message_id}` (Req 1.6) |
| `whatsapp_send` | `wa_send:{message_outbound_id}` |
| `logistics_prepare` | `logistics:{order_id}` (Req 8.12, 9.5) |
| `payment_confirmation_reply` | `pay_reply:{order_id}` (Req 8.12) |
| `tracking_notification` | `track:{order_id}` (Req 9.6) |
| `escalation_context_persist` | `esc_ctx:{conversation_id}` |

The `dispatched_actions` table provides DB-level dedup for the four order-keyed actions; the queue layer adds soft dedup for in-process replays.

---

## Configuration and Deployment

### Settings (`app/config.py`)

```python
from pydantic import Field, SecretStr, AnyHttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, case_sensitive=False, extra="ignore")

    # LLM
    LLM_PROVIDER: Literal["openai", "anthropic", "google"]
    LLM_MODEL: str = Field(min_length=1, max_length=200)
    OPENAI_API_KEY: SecretStr | None = None
    ANTHROPIC_API_KEY: SecretStr | None = None
    GOOGLE_API_KEY: SecretStr | None = None

    # Database
    DATABASE_URL: str

    # HTTP
    PORT: int = Field(default=8080, ge=1, le=65535)

    # WhatsApp Gateway (Node.js + Baileys)
    WHATSAPP_GATEWAY_URL: AnyHttpUrl                    # e.g. http://whatsapp_gateway:3001
    WHATSAPP_GATEWAY_INTERNAL_TOKEN: SecretStr          # Bearer token for gateway ↔ backend auth
    WHATSAPP_GATEWAY_AUTH_DIR: str = "/data/auth_state" # Baileys auth state directory (gateway side)
    WHATSAPP_BACKEND_INBOUND_URL: AnyHttpUrl            # URL the gateway calls: .../internal/whatsapp/inbound

    # Payment
    PAYMENT_PROVIDER: str = "sandbox"
    PAYMENT_PROVIDER_BASE_URL: AnyHttpUrl
    PAYMENT_PROVIDER_API_KEY: SecretStr
    PAYMENT_WEBHOOK_SECRET: SecretStr

    # RAG
    RAG_TOP_K: int = Field(default=5, ge=1, le=20)
    RAG_SIMILARITY_THRESHOLD: float = Field(default=0.7, ge=0.0, le=1.0)
    EMBEDDING_MODEL: str = "text-embedding-3-small"

    # Escalation
    ESCALATION_CONFIDENCE_THRESHOLD: float = Field(default=0.6, ge=0.0, le=1.0)

    # Admin
    ADMIN_BEARER_AUDIENCE: str = "wa-sales-admin"

    @model_validator(mode="after")
    def _validate_provider_credentials(self):
        provider = self.LLM_PROVIDER.lower()
        required = {"openai": self.OPENAI_API_KEY, "anthropic": self.ANTHROPIC_API_KEY, "google": self.GOOGLE_API_KEY}[provider]
        if required is None or not required.get_secret_value():
            raise StartupConfigError(f"Missing API key for LLM_PROVIDER={provider}")  # Req 12.4
        if not self.WHATSAPP_GATEWAY_URL:
            raise StartupConfigError("Missing WHATSAPP_GATEWAY_URL")
        return self
```

`StartupConfigError` is raised inside `app/main.py`'s lifespan and aborts startup before binding the listener (Req 12.3, 12.4, 12.10).

### Health endpoints (`app/api/health.py`)

```python
@router.get("/healthz")
async def healthz():
    return {"status": "ok", "ts": datetime.now(UTC).isoformat()}    # Req 12.6 (≤1s, no IO)

@router.get("/readyz")
async def readyz(db: AsyncSession = Depends(...), ckpt: PostgresSaver = Depends(...)):
    deadline = monotonic() + 5.0                                    # Req 12.7
    try:
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=2.0)
        await asyncio.wait_for(ckpt.aget_tuple(RunnableConfig(configurable={"thread_id": "_readyz"})), timeout=2.0)
        return {"status": "ready"}
    except Exception as e:
        return JSONResponse({"status": "not_ready", "dependency": classify_dep(e)}, status_code=503)
```

### Dockerfile (multi-stage with uv)

```dockerfile
# syntax=docker/dockerfile:1.6
FROM python:3.11-slim AS builder
ENV UV_LINK_MODE=copy UV_NO_CACHE=0 UV_COMPILE_BYTECODE=1
RUN pip install --no-cache-dir uv==0.4.*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app /app/app
COPY scripts /app/scripts
ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
```

### docker-compose.yml

```yaml
version: "3.9"
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: wa_agent
      POSTGRES_PASSWORD: wa_agent
      POSTGRES_DB: wa_agent
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U wa_agent"]
      interval: 5s
      timeout: 5s
      retries: 10
  api:
    build: .
    depends_on:
      postgres:
        condition: service_healthy
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://wa_agent:wa_agent@postgres:5432/wa_agent
    ports: ["8080:8080"]
volumes:
  pgdata: {}
```

### Cloud Run readiness checklist

- Listens on `$PORT` (Req 12.9, 12.10).
- Stateless container (no on-disk session state, no in-memory cache used as source of truth).
- All secrets via env vars (Req 12.11) — sourced from Google Secret Manager at deploy time.
- Graceful shutdown: FastAPI `lifespan` cancels worker tasks, drains `audit_flusher` queue with a 10 s budget.
- Liveness `/healthz` + readiness `/readyz` (Req 12.6–12.8).
- `min_instances=1` recommended to keep the in-process queue warm; the reconciler covers the case where it scales to zero.

---

## Local Development with uv

### `pyproject.toml` outline

```toml
[project]
name = "wa-sales-agent"
version = "0.1.0"
requires-python = ">=3.11"
description = "WhatsApp Sales Agent MVP"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.7",
  "pydantic-settings>=2.4",
  "sqlalchemy[asyncio]>=2.0",
  "asyncpg>=0.29",
  "alembic>=1.13",
  "pgvector>=0.3",
  "httpx>=0.27",
  "phonenumbers>=8.13",
  "structlog>=24.1",
  "langchain>=0.3",
  "langgraph>=0.2",
  "langgraph-checkpoint-postgres>=1.0",
  "langchain-openai>=0.2",
  "langchain-anthropic>=0.2",
  "langchain-google-genai>=2.0",
  "tenacity>=9.0",
  "python-multipart>=0.0.9",
]

[dependency-groups]
dev = [
  "pytest>=8.2",
  "pytest-asyncio>=0.23",
  "pytest-cov>=5.0",
  "hypothesis>=6.108",
  "ruff>=0.5",
  "mypy>=1.10",
  "respx>=0.21",
  "testcontainers[postgres]>=4.7",
]

[tool.uv]
package = false
```

### `.env.example`

```
# LLM
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=replace-me
ANTHROPIC_API_KEY=replace-me
GOOGLE_API_KEY=replace-me

# Database
DATABASE_URL=postgresql+asyncpg://wa_agent:wa_agent@localhost:5432/wa_agent

# Server
PORT=8080

# WhatsApp Gateway (Node.js + Baileys)
WHATSAPP_GATEWAY_URL=http://localhost:3001
WHATSAPP_GATEWAY_INTERNAL_TOKEN=replace-me
WHATSAPP_GATEWAY_AUTH_DIR=/data/auth_state
WHATSAPP_BACKEND_INBOUND_URL=http://localhost:8080/internal/whatsapp/inbound

# Payment
PAYMENT_PROVIDER=sandbox
PAYMENT_PROVIDER_BASE_URL=https://sandbox.example.com
PAYMENT_PROVIDER_API_KEY=replace-me
PAYMENT_WEBHOOK_SECRET=replace-me

# RAG / Embeddings
RAG_TOP_K=5
RAG_SIMILARITY_THRESHOLD=0.7
EMBEDDING_MODEL=text-embedding-3-small

# Escalation
ESCALATION_CONFIDENCE_THRESHOLD=0.6
```

### `scripts/migrate_and_seed.py`

```python
"""Idempotent migration + seed.

Steps:
1. Run Alembic 'upgrade head' against DATABASE_URL.
2. Ensure pgvector extension: CREATE EXTENSION IF NOT EXISTS vector.
3. Seed at least 5 products (with at least 1 variant each) and 5 FAQ rows
   using INSERT ... ON CONFLICT (sku) DO NOTHING / (title) DO NOTHING.
4. For every product/FAQ row updated since last run, recompute embeddings
   via EmbeddingService and upsert into product_embeddings / faq_embeddings.
5. Exit 0 on success, non-zero on any step failure with a descriptive log line.

This satisfies Req 13.6, 13.7, 13.8.
"""
```

### README.md outline

1. Prerequisites: Python ≥3.11, `uv` ≥0.4, Docker (optional for compose).
2. `uv sync` setup.
3. Copy `.env.example` to `.env` and fill values.
4. Start Postgres: `docker compose up -d postgres`.
5. Migrate + seed: `uv run python scripts/migrate_and_seed.py`.
6. Run API: `uv run uvicorn app.main:app --reload --port 8080`.
7. Health check: `curl http://localhost:8080/healthz`.
8. Tests: `uv run pytest` (unit + Hypothesis), `uv run pytest -m integration`.
9. Switching LLM provider: change `LLM_PROVIDER` and matching `*_API_KEY`.

---

## Embedding and RAG Pipeline

### Embedding model

- Configurable via `EMBEDDING_MODEL` (default `text-embedding-3-small`, dimension 1536).
- For Anthropic/Google, the same env var selects a provider-appropriate model (e.g., `text-embedding-004` for Google) — the embedding client is built inside `vectorstore/ingestion.py` from the same provider as the LLM_Factory by default but can be overridden via `EMBEDDING_PROVIDER`.

### When embeddings are generated

- **On insert/update of `products` or `faq_documents`**: a synchronous post-commit hook in the repository computes embeddings for changed chunks and upserts via `INSERT ... ON CONFLICT (product_id, chunk_index) DO UPDATE`.
- For MVP this is **synchronous on write**; Req 3.2 only requires the index to reflect changes within 5 minutes, which a synchronous write trivially satisfies.
- Bulk seed (`migrate_and_seed.py`) computes embeddings in batches of 50 to keep within rate limits.

### `RAGRetriever` contract

```python
class RAGRetriever:
    async def retrieve(self, query: str, *, top_k: int | None = None,
                       similarity_threshold: float | None = None) -> list[RetrievedChunk]:
        """
        Returns up to top_k chunks (default RAG_TOP_K) whose cosine similarity
        is >= similarity_threshold (default RAG_SIMILARITY_THRESHOLD).
        Empty list when no chunks meet the threshold (Req 3.5 graceful handling).
        Time budget: 2 seconds (Req 3.3).
        """
```

The retrieval SQL is a single round-trip:

```sql
SELECT id, source_kind, source_id, chunk_index, chunk_text,
       1 - (embedding <=> $1) AS similarity
FROM (
  SELECT id, 'product' AS source_kind, product_id AS source_id, chunk_index, chunk_text, embedding
  FROM product_embeddings
  UNION ALL
  SELECT id, 'faq', faq_id, chunk_index, chunk_text, embedding
  FROM faq_embeddings
) e
WHERE 1 - (embedding <=> $1) >= $2
ORDER BY e.embedding <=> $1
LIMIT $3;
```

(`<=>` is pgvector's distance operator; `1 - distance` gives cosine similarity.)

---

## Additional Sequence Diagrams

### Inbound message → reply (happy path, RAG Q&A)

```mermaid
sequenceDiagram
    autonumber
    participant WA_GW as WhatsApp Gateway (Baileys)
    participant API as /internal/whatsapp/inbound
    participant DB as PostgreSQL
    participant Q as WorkerQueue
    participant AGT as LangGraph Agent
    participant RAG as RAGRetriever
    participant CAT as CatalogService
    participant W as WhatsAppSender
    WA_GW->>API: POST inbound text "ada moisturizer kulit berminyak?" (Bearer token)
    API->>API: verify Bearer token (WHATSAPP_GATEWAY_INTERNAL_TOKEN, constant-time)
    API->>DB: INSERT messages_inbound (idempotent on baileys_message_id)
    API->>DB: get_or_create customer + conversation (E.164)
    API->>Q: enqueue agent_turn (dedupe=agent:msg_id)
    API-->>WA_GW: 200 OK (≤5s)
    Q->>AGT: invoke
    AGT->>DB: PostgresSaver.aget_tuple(thread=conv_id)
    AGT->>AGT: route_intent → inquiry
    AGT->>RAG: retrieve(query)
    RAG->>DB: pgvector ANN search (≤2s)
    RAG-->>AGT: top-K chunks
    AGT->>CAT: get_variant_with_price_and_stock for any specific attribute
    CAT->>DB: SELECT current_price, available_stock
    CAT-->>AGT: VariantSnapshot
    AGT->>AGT: response_generator (LLM, only grounded text)
    AGT->>DB: PostgresSaver.aput(state)
    AGT->>Q: enqueue whatsapp_send (4096-split)
    Q->>W: deliver
    W->>WA_GW: POST /send (Bearer token, ≤10s, retry 1/2/4s, max 3)
    W->>DB: UPDATE messages_outbound.status
```

### Duplicate payment webhook delivery (idempotent path)

```mermaid
sequenceDiagram
    autonumber
    participant PP as Payment Provider
    participant API as /webhooks/payment
    participant PAY as PaymentService
    participant DB as PostgreSQL
    participant Q as WorkerQueue

    PP->>API: POST webhook (event_id=E1, retry #2)
    API->>API: signature verify (≤500ms)
    API->>PAY: process_webhook(event)
    PAY->>DB: BEGIN
    PAY->>DB: INSERT payment_webhook_events(E1) ON CONFLICT DO NOTHING
    Note over DB: row already exists from first delivery → 0 rows inserted
    PAY->>DB: COMMIT
    PAY-->>API: (200, {"status":"duplicate"}) (≤1s)
    API-->>PP: 200 OK
    Note over Q: NO logistics task enqueued (Req 8.5)
    Note over Q: NO payment confirmation reply enqueued
```

### Cart confirm → order create → payment link

```mermaid
sequenceDiagram
    autonumber
    participant U as Customer
    participant AGT as Agent
    participant CAT as CatalogService
    participant ORD as OrderService
    participant PAY as PaymentService
    participant PROV as PaymentProvider(sandbox)
    participant DB as PostgreSQL
    U->>AGT: "ya, lanjutkan checkout"
    AGT->>AGT: derive customer_confirmation_token from cart snapshot
    AGT->>ORD: create_order(customer_id, cart_id, token)
    ORD->>DB: BEGIN; SELECT cart FOR UPDATE; recompute token
    alt token mismatch
        ORD-->>AGT: ToolError(token_mismatch) — Req 6.3
    else token matches
        ORD->>CAT: re-validate stock + price (≤5s)
        alt stock insufficient or price changed
            ORD->>DB: UPDATE carts.confirmation_token_invalidated_at (price drift only)
            ORD-->>AGT: ToolError(insufficient_stock | price_changed)
        else valid
            ORD->>DB: INSERT orders (status=pending_payment), order_items
            ORD->>DB: UPDATE carts.status=CONVERTED, link order_id
            ORD->>DB: COMMIT
            ORD-->>AGT: ok(order_id, total, currency)
            AGT->>PAY: create_payment_link(order_id)
            alt existing reusable payment
                PAY->>DB: SELECT existing payment WHERE status IN ('paid','created' AND expires_at>now)
                PAY-->>AGT: ok(existing link)  Req 7.4
            else needs new link
                PAY->>PROV: POST /payment-links (≤10s)
                alt provider unavailable
                    PAY-->>AGT: ToolError(provider_unavailable) Req 7.5
                else success
                    PAY->>DB: INSERT payments(status=created, link, expires_at)
                    PAY-->>AGT: ok(link)
                end
            end
            AGT->>U: "Total Rp X. Bayar di sini: <link>"
        end
    end
```

---

## Error Handling

### Retries and Timeouts

| External call / boundary | Per-attempt timeout | Retry policy | Failure semantics |
| --- | --- | --- | --- |
| WhatsApp Gateway `POST /send` | 10 s (Req 1.9) | 1 s → 2 s → 4 s capped 8 s, **max 3 attempts** (Req 1.10) | Persist `messages_outbound.status='failed'`, audit `outbound_send_failure`. No raise to inbound handler. |
| Inbound forwarder `POST /internal/whatsapp/inbound` (gateway→backend) | 10 s (gateway side, Req 1.5) | 1 s → 2 s → 4 s capped 8 s, **max 3 attempts** (Req 1.7); on exhaustion → on-disk overflow queue, re-attempted every 30 s | Gateway retries until backend is reachable; backend deduplicates by Baileys message id (Req 1.6). |
| Payment provider create-link (`POST`) | 10 s (Req 7.2) | None for the synchronous tool call (we don't want to keep the agent waiting). Fail fast → `provider_unavailable`. | Order remains `pending_payment` (Req 7.5). |
| Payment provider webhook signature verify | 500 ms (Req 8.2) | n/a | 401, no DB writes, audit `signature_*` (Req 8.3). |
| Database transactions (services) | 5 s overall budget (Req 6.4, 8.6, 9.2) | Outer caller may retry once on serialization failure (`SQLSTATE 40001`). | Surface `ToolError("db_transient")`; consecutive failures count toward escalation. |
| Embedding model | 5 s | 1 s → 2 s, max 2 attempts | Skip indexing for that chunk; log; reconciler retries within 60 s. |
| RAG retriever | 2 s (Req 3.3) | None | Empty list → graceful no-result handling (Req 3.5). |
| LangGraph PostgresSaver load | 5 s (Req 2.5) | None | Set escalation flag, audit, surface non-success (Req 2.7). |
| LangGraph PostgresSaver commit | 5 s (Req 2.6) | None | Suppress reply, set escalation, surface non-success (Req 2.9). |
| Audit log async write | 5 s | 1 s → 2 s → 4 s, max 3 attempts (Req 11.7) | Drain to `audit_log_failures`. |
| Escalation context persist | 5 s (Req 10.5) | ≥1 s backoff, max 3 attempts (Req 10.6) | Leave escalation set; log; surface failure to caller. |
| Catalog tool (search/cart) | 5 s (Req 3.7, 3.10) | None | Two consecutive failures → escalation (Req 3.11). |

### Error response envelope (tool-level)

```json
{
  "ok": false,
  "error": {
    "code": "insufficient_stock",
    "message": "human-readable",
    "details": { "variant_id": "uuid", "requested": 3, "available": 1 }
  }
}
```

The agent never surfaces internal codes verbatim to the customer (Non-Negotiable Rule). It maps codes to friendly messages, e.g., `insufficient_stock` → "Maaf, stok tidak cukup. Mau saya carikan alternatifnya?".

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

The properties below are PBT-ready: each one is universally quantified, references the requirements it validates, and has an executable check defined in the Testing Strategy section. They follow the consolidation performed in the prework analysis (29 properties covering Requirements 1 through 12; smoke checks for Requirement 13 are listed in Testing Strategy).

### Property 1: WhatsApp inbound bearer-token auth gate

*For any* request to `POST /internal/whatsapp/inbound` whose `Authorization` header is missing, malformed, or whose bearer token does not equal `WHATSAPP_GATEWAY_INTERNAL_TOKEN` (verified with a constant-time comparison), the API SHALL respond with HTTP 401 and SHALL produce no `messages_inbound` row, no `conversations` row, and no enqueued `agent_turn` task as a result of that request.

**Validates: Requirements 1.8**

### Property 2: WhatsApp inbound idempotency by Baileys message id

*For any* verified inbound `InboundEvent` payload `M` delivered one or more times to `POST /internal/whatsapp/inbound`, after processing there SHALL be exactly one row in `messages_inbound` with `baileys_message_id == M.baileys_message_id`, exactly one outbound reply attempt enqueued for that message id, and (for status updates) exactly one update applied per `(message_id, status)` pair.

**Validates: Requirements 1.6, 1.8**

### Property 3: WhatsApp outbound send retry policy

*For any* sequence of upstream WhatsApp Gateway `POST /send` responses, the sender SHALL make at most 3 attempts with backoffs `[1s, 2s, 4s]` (capped at 8s for any future attempt), SHALL stop on the first successful attempt, SHALL persist a `messages_outbound` row reflecting the final `sent` or `failed` status, and SHALL NOT raise an unhandled error to the inbound handler.

**Validates: Requirements 1.10**

### Property 4: Phone normalization idempotence and invalid rejection

*For any* string `s`, `normalize_to_e164(s)` SHALL return either `None` (invalid) or a string starting with `+` whose digit count is in `[8, 15]`; and *for any* `s` such that `normalize_to_e164(s) is not None`, `normalize_to_e164(normalize_to_e164(s)) == normalize_to_e164(s)` (idempotence). When the result is `None`, no `Conversation` SHALL be created and an audit record SHALL be written.

**Validates: Requirements 2.1, 2.2**

### Property 5: Checkpointer ordering — load before run, commit before send

*For any* agent turn, the `PostgresSaver.aget_tuple(thread_id=conversation_id)` call SHALL complete before any tool is invoked, and `PostgresSaver.aput(state)` SHALL complete before any outbound `whatsapp_send` task is enqueued for the same turn.

**Validates: Requirements 2.4, 2.5, 2.6**

### Property 6: Checkpointer failure escalates before auditing and surfaces non-success

*For any* checkpoint load or commit failure during a turn, the system SHALL set the `Escalation_Flag` on the conversation atomically, SHALL only write the corresponding audit record after the flag has been persisted, SHALL NOT enqueue an outbound reply for that turn, and SHALL surface a non-success response to the inbound webhook so re-delivery is possible.

**Validates: Requirements 2.7, 2.8, 2.9**

### Property 7: RAG retrieval bounded by top-K and similarity threshold

*For any* query string and *for any* configured `RAG_TOP_K ∈ [1, 20]` and `RAG_SIMILARITY_THRESHOLD ∈ [0.0, 1.0]`, `RAGRetriever.retrieve(query)` SHALL return a list of length `≤ RAG_TOP_K` whose every element has `similarity ≥ RAG_SIMILARITY_THRESHOLD`. *For any* query yielding zero results above the threshold, the agent's reply SHALL be a single clarifying question and the consecutive-RAG-miss counter for that conversation SHALL be incremented.

**Validates: Requirements 3.3, 3.5**

### Property 8: Two consecutive failures of the same class trigger escalation

*For any* conversation, *for any* sequence of agent turns that contains two consecutive RAG-misses below the similarity threshold OR two consecutive Catalog-tool failures/timeouts, the `Escalation_Flag` SHALL be set on the conversation after the second occurrence and no further automated reply SHALL be produced. The same property SHALL hold whenever the agent's intent classifier identifies the customer message as a request for human support, refund/cancellation, or a payment problem report, OR whenever the candidate-reply confidence falls below `ESCALATION_CONFIDENCE_THRESHOLD`.

**Validates: Requirements 3.6, 3.11, 10.1, 10.2**

### Property 9: Tool input shape validation rejects malformed inputs without state mutation

*For any* tool invocation whose input does not match the declared Pydantic schema (including out-of-range integers, strings outside the declared `[1, N]` length range, or unknown extra fields), the tool SHALL return a structured validation error with a machine-readable code, SHALL NOT contact any external provider, and SHALL leave all `Cart`, `Order`, `Payment`, `Shipment`, and `Customer` rows unchanged.

**Validates: Requirements 3.8, 5.2, 5.3, 5.6, 6.1, 7.1, 9.1**

### Property 10: Cart subtotal invariant

*For any* customer and *for any* sequence of successful cart-mutating tool invocations (`add_to_cart`, `update_cart_item_quantity`, `remove_from_cart`), after each successful invocation the active cart SHALL satisfy `cart.subtotal == SUM(cart_item.quantity * cart_item.unit_price_snapshot)` over all persisted `cart_items` rows of that cart.

**Validates: Requirements 5.7**

### Property 11: Cart-item unit price snapshot is frozen at write time

*For any* `Cart_Item` written by `add_to_cart` or `update_cart_item_quantity`, the persisted `unit_price_snapshot` SHALL equal the `Product_Variant.current_price` observed at the moment of that operation, and SHALL remain unchanged when `Product_Variant.current_price` is later updated.

**Validates: Requirements 5.5**

### Property 12: At most one OPEN cart per customer

*For any* sequence of cart operations applied to the same customer, the database SHALL contain at most one `carts` row with `status = 'OPEN'` and `customer_id = c` at any committed snapshot. *For any* cart-mutating tool invoked when no OPEN cart exists for the customer, an OPEN cart SHALL be created before the mutation is applied.

**Validates: Requirements 5.1, 5.10**

### Property 13: Validation failure leaves state unchanged

*For any* tool invocation that fails its declared validation rules (`customer_not_found`, `product_not_found`, `variant_not_found`, `variant_product_mismatch`, `invalid_quantity`, `insufficient_stock`, `cart_item_not_found`, `token_mismatch`, `price_changed`, `order_not_found`, `order_not_paid`, `provider_unavailable`), the database state observed before the call SHALL be byte-equivalent to the database state observed after the call (no row inserted, updated, or deleted), and the error response SHALL contain the corresponding machine-readable code.

**Validates: Requirements 5.4, 5.8, 6.3, 6.5, 6.6, 7.3, 7.5, 9.3, 9.4**

### Property 14: Confirmation token determinism and mismatch rejection

*For any* cart snapshot `S = (cart_id, [(item_id, quantity, unit_price_snapshot)], currency)`, `derive_token(S)` SHALL be deterministic (same `S` always produces the same token) and *for any* `S' ≠ S`, `derive_token(S') ≠ derive_token(S)` (collision-free up to SHA-256). *For any* `create_order(customer_id, cart_id, t)` where `t ≠ derive_token(current_snapshot(cart_id))`, the call SHALL be rejected with `token_mismatch` and the cart SHALL remain unchanged.

**Validates: Requirements 6.2, 6.3**

### Property 15: Order total is backend-computed and never LLM-supplied

*For any* successful `create_order` invocation, the persisted `orders.total` SHALL equal `SUM(cart_item.quantity * cart_item.unit_price_snapshot) + orders.backend_fees` over the converted cart, and the `OrderService.create_order` interface SHALL NOT accept any monetary value as a parameter, ensuring the LLM cannot influence the persisted total. *For any* outbound reply that mentions the order total or order id, the value SHALL appear verbatim as returned by the `create_order` tool response.

**Validates: Requirements 6.7, 6.9**

### Property 16: Initial order status is `pending_payment` and links to the originating cart

*For any* successful `create_order(customer_id, cart_id, token)` invocation, the resulting `orders` row SHALL have `status = 'pending_payment'`, `cart_id = cart_id`, `customer_id = customer_id`, and the originating `carts` row SHALL have `status = 'CONVERTED'` and `order_id` pointing to the new order.

**Validates: Requirements 6.8**

### Property 17: Payment-link reuse on existing valid payment

*For any* `Order` with an existing `Payment` whose `status = 'paid'` OR (`status = 'created'` AND `expires_at > now()`), `create_payment_link(order_id)` SHALL return the existing payment link, status, and expiry without contacting the Payment_Provider and without inserting a new `payments` row.

**Validates: Requirements 7.4**

### Property 18: Agent delivers payment URL verbatim

*For any* successful `create_payment_link` tool response containing `link_url = U`, the outbound WhatsApp text body produced for that turn SHALL contain `U` as an exact substring without alteration, shortening, wrapping, or substitution.

**Validates: Requirements 7.6**

### Property 19: Payment webhook idempotency by `webhook_event_id`

*For any* signature-verified payment webhook with `webhook_event_id = E` delivered one or more times, after all deliveries are processed there SHALL be exactly one `payment_webhook_events` row with `webhook_event_id = E`; the resulting `(payments.status, orders.status)` SHALL be the same as if `E` had been delivered exactly once; and the number of rows in `dispatched_actions` for the affected `(order_id, action_type)` pairs SHALL be exactly one each.

**Validates: Requirements 8.4, 8.5, 8.7**

### Property 20: Payment success state transition is atomic

*For any* signature-verified webhook indicating outcome `O ∈ {paid, failed, expired}` for a `Payment` whose pre-image status is `created`, the post-state SHALL satisfy:

- `payments.status = O`
- `orders.status = 'paid'` iff `O = 'paid'`, else `orders.status = 'pending_payment'`
- `payment_webhook_events(webhook_event_id) exists`
- `payments.verification_metadata` contains the provider reference, provider event id, payment timestamp, and raw payload (when `O = 'paid'`)

All of the above SHALL hold within a single committed transaction, OR none SHALL hold (no partial commit).

**Validates: Requirements 8.6, 8.8, 8.9**

### Property 21: Legal Order/Payment state machine and "no shipment before paid"

*For any* sequence of webhook deliveries and tool invocations, every observed transition of `orders.status` and `payments.status` SHALL be one of the legal transitions defined in the State Machines section. In particular, `orders.status = 'shipment_prepared'` SHALL imply that there exists a committed `payments` row for that order with `status = 'paid'` whose `paid_at <= orders.shipment_prepared_at`. No `shipments` row SHALL exist for an order whose `status` is or has ever been `pending_payment` without first transitioning through `paid`.

**Validates: Requirements 6.8, 8.6, 8.8, 8.9, 9.4**

### Property 22: Single dispatch per `(order_id, action_type)`

*For any* `order_id` and *for any* `action_type ∈ {logistics_prepare, payment_confirmation_reply, tracking_notification}`, the database SHALL contain at most one row in `dispatched_actions` matching that pair, and the corresponding worker task SHALL execute at most once per `(order_id, action_type)` pair, regardless of webhook re-deliveries, container restarts, or service-level retries.

**Validates: Requirements 8.12, 9.5, 9.6**

### Property 23: Shipment tracking number well-formedness and uniqueness

*For any* successful `prepare_shipment(order_id)` invocation, the resulting `shipments` row SHALL have `tracking_number` matching the regex `^[A-Za-z0-9]{8,32}$`, and *for any* two shipments in the database, `s1.tracking_number ≠ s2.tracking_number`. The shipment row and the `orders.status = 'shipment_prepared'` update SHALL be persisted in a single transaction.

**Validates: Requirements 9.2**

### Property 24: Escalation suppresses automated replies and queues inbound for human review

*While* `conversations.escalation_flag = TRUE`, *for any* inbound message delivered for that conversation, the system SHALL NOT enqueue any `whatsapp_send` task for an automated reply, SHALL persist the inbound message, and SHALL place the message into the human-review queue. The flag SHALL only be cleared via an authenticated and authorized admin call to `POST /admin/conversations/{id}/resume`; unauthorized attempts SHALL leave the flag unchanged and produce an audit record.

**Validates: Requirements 10.3, 10.4, 10.5, 10.7, 10.8, 10.9**

### Property 25: Audit exactly-once per dedupe key

*For any* targeted action `A` (a tool invocation identified by its tool-invocation id, or a webhook identified by its `webhook_event_id`), after `A` is processed, the union of `audit_logs` and `audit_log_failures` SHALL contain exactly one record with `dedupe_key = key(A)`. The synchronous emit path on the user-facing request SHALL block for at most 100 milliseconds.

**Validates: Requirements 11.1, 11.2, 11.6**

### Property 26: Audit redaction

*For any* input or output passed to `AuditLogger.emit(record)` containing a credential-like substring (matching patterns for `Authorization` headers, `*_API_KEY`, `*_SECRET`, full payment account numbers of 12–19 digits, or the configured secret env var values), the persisted `input_redacted` and `output_redacted` JSON SHALL NOT contain that substring; the substring SHALL be replaced by a fixed marker (e.g. `"[REDACTED]"`).

**Validates: Requirements 11.8**

### Property 27: Audit no-loss with durable failure queue

*For any* sequence of audit emit calls and *for any* sequence of write failures (with retry policy 1s/2s/4s × 3 attempts), every emitted action SHALL eventually result in exactly one row residing either in `audit_logs` (on success) or in `audit_log_failures` (when all retries are exhausted). The total record count across both tables SHALL equal the number of distinct emitted actions (counted by `dedupe_key`).

**Validates: Requirements 11.7**

### Property 28: LLM_Factory provider selection and startup fail-fast

*For any* string `p` such that `p.lower() ∈ {"openai", "anthropic", "google"}` and the corresponding credential env var is non-empty, `build_llm_factory({LLM_PROVIDER: p, LLM_MODEL: m, ...})` SHALL return a chat-model factory whose underlying class matches `p`. *For any* `p` outside that set, or any missing `LLM_MODEL`, or any missing required credential, startup SHALL terminate within 5 seconds, the HTTP listener SHALL NOT bind, and an error log identifying the offending variable SHALL be emitted.

**Validates: Requirements 12.1, 12.2, 12.3, 12.4**

### Property 29: Health endpoint contract

*For any* request to `GET /healthz`, the API SHALL respond with HTTP 200 and a JSON body within 1 second, and the handler SHALL not perform any database query or external network call. *For any* request to `GET /readyz`:

- if the database connection check and the checkpointer reachability check both succeed within 5 seconds, the response SHALL be HTTP 200 with `{"status":"ready"}`;
- otherwise the response SHALL be HTTP 503 with a JSON body identifying the unavailable dependency name (`"db"` or `"checkpointer"`).

**Validates: Requirements 12.6, 12.7, 12.8**

---

## Testing Strategy

### Library and tooling choices

- **Test runner**: `pytest` with `pytest-asyncio` for async tests, `pytest-cov` for coverage.
- **Property-based testing**: `Hypothesis` for Python, configured with `max_examples=100` for property tests on pure functions, `max_examples=50` for property tests with database fixtures (slower), and per-test stateful machines for sequence-based properties (`hypothesis.stateful`).
- **HTTP fakes**: `respx` for mocking `httpx` calls to the WhatsApp Gateway (`POST /send`) and Payment_Provider sandbox.
- **Database integration**: `testcontainers[postgres]` to spin up a real `pgvector/pgvector:pg16` container per test session, with per-test transactional rollback.
- **LLM stubs**: a `FakeChatModel` injected via `LLMFactory` override for deterministic unit tests; structured-output paths are tested with hand-crafted classifier outputs.

### Tagging convention for property-based tests

Each property test is tagged with a docstring referencing the design property number:

```python
@given(...)
@settings(max_examples=200)
def test_p10_cart_subtotal_invariant(...):
    """
    Feature: whatsapp-sales-agent-mvp, Property 10: Cart subtotal invariant
    """
```

This makes the requirement → design-property → test mapping fully traceable and is enforced by a CI lint check (`scripts/check_property_tags.py`).

### Layered test pyramid

#### 1. Unit tests (per service, per utility)

- `utils/phone.py`: idempotence + invalid rejection (Property 4).
- `utils/signature.py`: HMAC equality vs randomized bytes (Property 1, and Property 19's signature gate).
- `utils/confirmation_token.py`: determinism + collision-free over snapshot variants (Property 14).
- `utils/redaction.py`: never-leaks scrubber (Property 26).
- Service layer pure helpers: total computation, status-transition validation.

#### 2. Property-based tests using Hypothesis

The 29 properties from the Correctness Properties section each map to one Hypothesis property test (numbered `test_pNN_*`). Sketches:

```python
# tests/properties/test_p01_whatsapp_bearer_gate.py
from hypothesis import given, strategies as st

@given(bad_token=st.text(max_size=128))
async def test_p01_bad_bearer_no_side_effects(http_client, db, bad_token):
    """Feature: whatsapp-sales-agent-mvp, Property 1: WhatsApp inbound bearer-token auth gate"""
    resp = await http_client.post("/internal/whatsapp/inbound",
                                  content=b'{"baileys_message_id":"test"}',
                                  headers={"Authorization": f"Bearer {bad_token}",
                                           "Content-Type": "application/json"})
    assert resp.status_code == 401
    assert await db.scalar(select(func.count()).select_from(messages_inbound)) == 0
    # And: no agent_turn task enqueued
```

```python
# tests/properties/test_p10_cart_subtotal.py
class CartStateMachine(RuleBasedStateMachine):
    """Feature: whatsapp-sales-agent-mvp, Property 10: Cart subtotal invariant"""
    customers = Bundle("customers")
    variants = Bundle("variants")
    cart_items = Bundle("cart_items")
    @rule(target=customers, ...) def add_customer(self, ...): ...
    @rule(target=variants, ...) def add_variant(self, ...): ...
    @rule(target=cart_items, customer=customers, variant=variants, q=st.integers(1, 999))
    def add_to_cart(self, customer, variant, q): ...
    @rule(item=cart_items, q=st.integers(1, 999))
    def update_qty(self, item, q): ...
    @rule(item=cart_items)
    def remove_item(self, item): ...
    @invariant()
    def subtotal_matches_sum(self):
        for cart in self.open_carts():
            expected = sum(i.quantity * i.unit_price_snapshot for i in cart.items)
            assert cart.subtotal == expected
```

```python
# tests/properties/test_p19_payment_webhook_idempotency.py
@given(event=payment_webhook_events_strategy(), n_replays=st.integers(min_value=1, max_value=10))
async def test_p19_duplicate_webhook_is_idempotent(payment_service, db, event, n_replays):
    """Feature: whatsapp-sales-agent-mvp, Property 19: Payment webhook idempotency by webhook_event_id"""
    await payment_service.process_webhook(event, raw_payload=b"...")
    pre = await snapshot(db, ["payments", "orders", "dispatched_actions", "payment_webhook_events"])
    for _ in range(n_replays - 1):
        await payment_service.process_webhook(event, raw_payload=b"...")
    post = await snapshot(db, ["payments", "orders", "dispatched_actions", "payment_webhook_events"])
    assert pre == post
```

Comparable tests exist for properties 2, 3, 5–9, 11–18, 20–29. State-machine style is used for properties that involve sequences (cart mutations, webhook re-deliveries, escalation counters).

#### 3. Integration tests with ephemeral PostgreSQL + pgvector

- Spin up `pgvector/pgvector:pg16` via testcontainers; run Alembic migrations once per session.
- Each test wraps in a SAVEPOINT-based rollback to keep tests isolated.
- Fixtures: `seeded_catalog`, `seeded_customer`, `confirmed_cart`, `paid_order`.

Categories:

- WhatsApp webhook end-to-end: signed request → DB rows → enqueue side effect.
- Payment webhook end-to-end with redelivery and out-of-order events.
- create_order ↔ create_payment_link ↔ payment webhook ↔ logistics chain.
- LangGraph PostgresSaver round-trip: persist state, restart connection pool, reload, assert equality.
- RAG retrieval against seeded embeddings to assert top-K + threshold (Property 7).

#### 4. Adversarial / security-focused tests

- Property 1 with random fuzzing on signature header.
- Property 19 with concurrent duplicate deliveries (race condition test using `asyncio.gather` and verifying DB unique constraint catches them).
- Property 22 single-dispatch under simulated container restart between commit and queue.enqueue (the reconciler must close the gap).
- Property 26 with a curated bank of credential-like patterns.

#### 5. Smoke tests for Requirements 12.5, 12.11, 12.12, 12.13, 13.x

- AST scan: `agent/` modules contain no `import langchain_openai|langchain_anthropic|langchain_google_genai` (Req 12.5).
- Secret scanner (e.g., `gitleaks`) over the repo for credentials (Req 12.11).
- `docker build .` succeeds; the container starts and `/healthz` answers within 30 s (Req 12.12).
- `docker compose up -d` succeeds; the API container reaches healthy state and `/readyz` returns 200 (Req 12.13).
- `uv sync` exit 0 within 600 s on a clean cache (Req 13.3).
- `python scripts/migrate_and_seed.py` runs twice; row counts identical between runs (Req 13.7).
- `.env.example` parses as KEY=value lines and contains every name read by `app/config.py::Settings` (Req 13.5).

### Test fakes for external providers

- **`FakeWhatsAppGateway`**: in-memory HTTP server stub that handles `POST /send` requests from the WhatsApp sender worker; configurable to inject HTTP errors, timeouts, or specific response codes; records every call for assertions.
- **`FakePaymentProvider`** + **`PaymentSandboxHarness`**: deterministic payment-link creation; signs simulated webhook payloads with `PAYMENT_WEBHOOK_SECRET` so the signature verifier passes; supports replaying the same `webhook_event_id` to drive Property 19 tests.
- **`FakeChatModel`**: implements LangChain's `BaseChatModel` interface. Returns scripted responses keyed by prompt fingerprint so deterministic tests of agent flow are stable.

### CI matrix

- `lint`: ruff + mypy.
- `unit`: `uv run pytest tests/unit -q`.
- `properties`: `uv run pytest tests/properties -q --hypothesis-show-statistics`.
- `integration`: `uv run pytest tests/integration -q -m integration` (requires Docker).
- `smoke`: docker build, compose up, end-to-end happy path.

### Coverage and quality gates

- Line coverage ≥ 85% on `app/services/`, `app/utils/`, `app/agent/`.
- Each named tool in `app/tools/` SHALL have at least one Hypothesis test.
- Each named property (1–29) SHALL have at least one corresponding test file.

---

## Requirements Traceability Matrix

| Req | Acceptance Criteria | Design Components | Properties |
| --- | --- | --- | --- |
| 1 WhatsApp transport via Baileys Gateway | 1.1–1.12 | `api/whatsapp.py` (`POST /internal/whatsapp/inbound`), `workers/whatsapp_sender.py`, `repositories/messages.py`, `schemas/whatsapp.py`, `whatsapp_gateway/` (Node.js + Baileys microservice) | P1, P2, P3 |
| 2 Conversation state + checkpointing | 2.1–2.10 | `agent/checkpointer.py`, `services/conversation.py`, `utils/phone.py`, `conversation_checkpoints` | P4, P5, P6 |
| 3 Catalog Q&A + RAG | 3.1–3.11 | `services/catalog.py`, `vectorstore/retriever.py`, `tools/catalog.py`, `product_embeddings`, `faq_embeddings` | P7, P8, P9, P13 |
| 4 Recommendation | 4.1–4.7 | `agent/nodes/recommend.py`, `tools/catalog.py::search_products` | P7, P8, P9 (subset), and the response-shape unit tests |
| 5 Cart management | 5.1–5.10 | `services/catalog.py` (cart submodule), `tools/catalog.py`, `carts`, `cart_items` | P9, P10, P11, P12, P13 |
| 6 Order creation | 6.1–6.9 | `services/order.py`, `tools/order.py`, `utils/confirmation_token.py`, `orders`, `order_items` | P9, P13, P14, P15, P16, P21 |
| 7 Payment link | 7.1–7.7 | `services/payment.py`, `tools/payment.py`, payment provider client interface, `payments` | P9, P13, P17, P18 |
| 8 Verified idempotent payment webhook | 8.1–8.12 | `api/payment.py`, `services/payment.py`, `payment_webhook_events`, `dispatched_actions` | P19, P20, P21, P22 |
| 9 Simulated logistics | 9.1–9.6 | `services/logistics.py`, `workers/logistics_worker.py`, `shipments`, `dispatched_actions` | P9, P13, P21, P22, P23 |
| 10 Human escalation | 10.1–10.9 | `services/human_support.py`, `services/conversation.py`, `api/admin.py`, `escalations` | P8, P24 |
| 11 Audit logging | 11.1–11.8 | `observability/audit_logger.py`, `services/audit.py`, `workers/audit_flusher.py`, `audit_logs`, `audit_log_failures` | P25, P26, P27 |
| 12 LLM_Factory + Cloud Run | 12.1–12.13 | `agent/llm_factory.py`, `app/config.py`, `api/health.py`, `Dockerfile`, `docker-compose.yml` | P28, P29; smoke tests for 12.5, 12.11, 12.12, 12.13 |
| 13 Local dev with uv | 13.1–13.11 | `pyproject.toml`, `uv.lock`, `.env.example`, `scripts/migrate_and_seed.py`, `README.md` | Smoke tests only |
