# WhatsApp Sales Agent

An AI-powered conversational commerce assistant for online sellers. The system receives WhatsApp messages, runs an AI sales/customer-service agent, answers product questions, manages carts and orders, generates payment links, processes payment callbacks, and prepares shipment after verified payment.

## Architecture

```
┌─────────────────┐       HTTP (internal)       ┌──────────────────────┐
│  WhatsApp User  │◄──── WhatsApp Web ────────►│  WhatsApp Gateway    │
│                 │                              │  (Node.js + Baileys) │
└─────────────────┘                              └──────────┬───────────┘
                                                            │
                                              Bearer-token auth
                                                            │
                                                            ▼
                                                 ┌──────────────────────┐
                                                 │  Python Backend      │
                                                 │  (FastAPI)           │
                                                 │                      │
                                                 │  ┌────────────────┐  │
                                                 │  │ LangGraph Agent│  │
                                                 │  │ (multi-turn)   │  │
                                                 │  └───────┬────────┘  │
                                                 │          │           │
                                                 │  ┌───────▼────────┐  │
                                                 │  │ Tools/Services │  │
                                                 │  │ (catalog, cart,│  │
                                                 │  │  order, payment│  │
                                                 │  │  logistics)    │  │
                                                 │  └───────┬────────┘  │
                                                 │          │           │
                                                 └──────────┼───────────┘
                                                            │
                                                            ▼
                                                 ┌──────────────────────┐
                                                 │  PostgreSQL + pgvector│
                                                 │  (pgvector/pgvector:  │
                                                 │   pg16)              │
                                                 └──────────────────────┘
```

The system is split into two services:

- **WhatsApp Gateway** (Node.js + Baileys) — owns the WhatsApp Web session, handles QR pairing, deduplicates inbound messages, and forwards them to the Python backend over an authenticated internal HTTP channel.
- **Python Backend** (FastAPI) — runs the LangGraph agent, manages conversation state with PostgreSQL-backed checkpoints, and orchestrates all commerce logic through deterministic backend services.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language (backend) | Python 3.11+ |
| Language (gateway) | TypeScript (Node.js 20) |
| Web framework | FastAPI + Uvicorn |
| AI orchestration | LangGraph + LangChain |
| LLM providers | OpenAI, Anthropic, Google (configurable) |
| Database | PostgreSQL 16 + pgvector |
| ORM / migrations | SQLAlchemy 2.0 (async) + Alembic |
| WhatsApp transport | Baileys (WhatsApp Web protocol) |
| HTTP gateway | Fastify |
| Observability | structlog, OpenTelemetry, Pino |
| Testing | pytest, Hypothesis (property-based), Vitest |
| Package management | uv (Python), pnpm (Node.js) |
| Containerization | Docker, Docker Compose |

## Prerequisites

- **Python** >= 3.11
- **uv** >= 0.4 ([install guide](https://docs.astral.sh/uv/getting-started/installation/))
- **Node.js** 20+ (for the WhatsApp gateway)
- **pnpm** (enable via `corepack enable`)
- **Docker** and **Docker Compose**
- A phone with WhatsApp installed (for QR pairing)

## Quick Start

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd whatsapp_sales_agent

# Python dependencies
uv sync

# Gateway dependencies
cd whatsapp_gateway
corepack enable
pnpm install
cd ..
```

### 2. Configure environment

```bash
cp .env.example .env  # or create .env manually
```

Key environment variables:

| Variable | Description |
|----------|-------------|
| `LLM_PROVIDER` | `openai`, `anthropic`, or `google` |
| `LLM_MODEL` | Model identifier (e.g. `gpt-4.1-mini`) |
| `OPENAI_API_KEY` | OpenAI API key (if using OpenAI) |
| `ANTHROPIC_API_KEY` | Anthropic API key (if using Anthropic) |
| `GOOGLE_API_KEY` | Google API key (if using Google) |
| `DATABASE_URL` | PostgreSQL connection string |
| `WHATSAPP_GATEWAY_URL` | Gateway URL (e.g. `http://localhost:3001`) |
| `WHATSAPP_GATEWAY_INTERNAL_TOKEN` | Shared bearer token for gateway ↔ backend auth |
| `WHATSAPP_BACKEND_INBOUND_URL` | Backend inbound URL (e.g. `http://localhost:8080/internal/whatsapp/inbound`) |
| `PAYMENT_PROVIDER_NAME` | Payment provider (`sandbox` for MVP) |
| `PAYMENT_WEBHOOK_SECRET` | HMAC secret for payment webhook verification |

See `app/config.py` for the full list of supported variables.

### 3. Start infrastructure

```bash
docker compose up -d postgres whatsapp_gateway
```

This starts:
- **PostgreSQL** (pgvector:pg16) on port `5432`
- **WhatsApp Gateway** on port `3001`

### 4. Pair WhatsApp (QR code)

```bash
# Fetch QR code
curl -s -H "Authorization: Bearer YOUR_TOKEN" http://localhost:3001/qr | jq .

# Save as image and open
curl -s -H "Authorization: Bearer YOUR_TOKEN" http://localhost:3001/qr \
  | jq -r '.qr_data_url' \
  | sed 's/data:image\/png;base64,//' \
  | base64 -d > /tmp/qr.png
open /tmp/qr.png
```

Scan the QR code from WhatsApp → Settings → Linked Devices → Link a Device.

Verify connection:
```bash
curl -s http://localhost:3001/readyz
# {"status":"connected"}
```

### 5. Run migrations and seed data

```bash
uv run python scripts/migrate_and_seed.py
```

This applies all Alembic migrations and seeds the database with sample products (skincare items) and FAQ documents.

### 6. Start the backend

```bash
uv run uvicorn app.main:app --port 8080 --reload
```

Verify:
```bash
curl http://localhost:8080/healthz
# {"status":"ok","ts":"..."}
```

## Project Structure

```
whatsapp_sales_agent/
├── app/
│   ├── main.py                 # FastAPI app factory + lifespan
│   ├── config.py               # Pydantic Settings (fail-fast validation)
│   ├── api/                    # HTTP route handlers
│   │   └── health.py           # /healthz, /readyz
│   ├── agent/                  # LangGraph agent
│   │   ├── state.py            # ConversationState TypedDict
│   │   ├── checkpointer.py     # PostgresSaver wiring
│   │   ├── llm_factory.py      # Provider-agnostic LLM factory
│   │   ├── nodes/              # Graph nodes (route_intent, retrieve_rag, etc.)
│   │   └── prompts/            # Prompt templates
│   ├── tools/                  # LangChain tools (catalog, order, payment, support)
│   ├── services/               # Business logic (transactions)
│   ├── repositories/           # Database access (narrow async functions)
│   ├── db/
│   │   ├── models.py           # SQLAlchemy models
│   │   ├── session.py          # Engine + session factory
│   │   └── migrations/         # Alembic migrations
│   ├── vectorstore/            # pgvector retriever
│   ├── workers/                # Async task workers
│   ├── schemas/                # Pydantic request/response schemas
│   ├── observability/          # Logging, tracing, metrics
│   └── utils/                  # Phone normalization, confirmation tokens, etc.
├── whatsapp_gateway/           # Node.js + Baileys microservice
│   ├── src/
│   │   ├── index.ts            # Fastify entry point
│   │   ├── baileys/            # Session management, inbound handler
│   │   ├── forwarder/          # HTTP forwarder with retry + overflow queue
│   │   ├── middleware/         # Bearer-token auth
│   │   ├── routes/             # /send, /qr, /healthz, /readyz, /metrics
│   │   └── utils/              # E.164 phone normalization
│   ├── Dockerfile
│   └── package.json
├── tests/
│   ├── unit/                   # Unit tests
│   ├── properties/             # Hypothesis property-based tests
│   ├── integration/            # Integration tests
│   └── smoke/                  # End-to-end smoke tests
├── scripts/
│   └── migrate_and_seed.py     # DB migration + seed script
├── docker-compose.yml
├── pyproject.toml
├── uv.lock
├── alembic.ini
└── .gitignore
```

## Agent Design

The LangGraph agent handles multi-turn WhatsApp conversations with the following node graph:

| Node | Responsibility |
|------|---------------|
| `route_intent` | Classifies customer intent with confidence score |
| `retrieve_rag` | Retrieves relevant product/FAQ context via pgvector |
| `search_catalog` | Searches product catalog with filters |
| `recommend` | Generates product recommendations |
| `manage_cart` | Add/remove/update cart items with stock validation |
| `request_confirmation` | Asks customer to confirm before checkout |
| `create_order` | Creates order with price/stock re-validation |
| `create_payment_link` | Generates payment link via provider |
| `escalate` | Routes to human support |
| `send_reply` | Enqueues WhatsApp reply (non-blocking) |

Key behaviors:
- 2 consecutive RAG misses or tool failures → automatic escalation
- Confidence below threshold → escalation with reply suppressed
- Conversation state persisted to PostgreSQL via LangGraph checkpointer
- All tool calls produce exactly one audit record

## Switching LLM Providers

The LLM provider is configurable at runtime via environment variables:

```bash
# OpenAI
LLM_PROVIDER=openai
LLM_MODEL=gpt-4.1-mini
OPENAI_API_KEY=sk-...

# Anthropic
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-20250514
ANTHROPIC_API_KEY=sk-ant-...

# Google
LLM_PROVIDER=google
LLM_MODEL=gemini-2.0-flash
GOOGLE_API_KEY=AI...
```

The agent code never imports provider-specific clients directly — all access goes through the LLM factory.

## Running Tests

```bash
# All Python tests
uv run pytest

# Unit tests only
uv run pytest tests/unit

# Property-based tests (Hypothesis)
uv run pytest tests/properties

# Integration tests
uv run pytest tests/integration

# Gateway tests
cd whatsapp_gateway && pnpm test
```

## Linting and Type Checking

```bash
# Python
uv run ruff check .
uv run mypy app

# Gateway
cd whatsapp_gateway && pnpm lint
```

## Docker Compose (Full Stack)

```bash
# Start all services
docker compose up -d

# Check status
docker compose ps

# View logs
docker compose logs -f

# Stop
docker compose down
```

Services:
- `postgres` — PostgreSQL 16 + pgvector (port 5432)
- `whatsapp_gateway` — Baileys gateway (port 3001)

## Key Design Principles

1. **Deterministic systems own transactions** — The LLM never decides prices, stock, payment status, or order state. All transactional actions are confirmed through backend services.

2. **Conversation state persists across messages** — WhatsApp conversations are async and multi-turn. State is checkpointed to PostgreSQL after every agent turn.

3. **Idempotency at every boundary** — Inbound messages are deduplicated by Baileys message ID. Payment webhooks are deduplicated by webhook event ID. Dispatched actions use unique constraints.

4. **Human escalation is part of the product** — The agent escalates when confidence is low, tools fail repeatedly, or the customer requests human support. Context is preserved for handoff.

5. **Audit everything important** — Tool calls, payment transitions, and escalations produce audit records with redacted sensitive data.

## WhatsApp Gateway

The gateway is documented separately in [`whatsapp_gateway/README.md`](./whatsapp_gateway/README.md). Key points:

- Connects to WhatsApp Web via Baileys (QR-code pairing)
- Persists auth state to disk (survives restarts)
- Deduplicates inbound messages via in-memory LRU
- Retries failed forwards with exponential backoff + overflow queue
- No messages are lost even if the backend is temporarily down

## Development Status

This project is under active development. The MVP focuses on:

- ✅ WhatsApp message receiving and sending
- ✅ LangGraph agent with multi-turn conversation
- ✅ Product catalog search and Q&A (RAG)
- ✅ Database schema and migrations
- ✅ LLM provider abstraction
- 🔲 Cart management with stock validation
- 🔲 Order creation and payment link generation
- 🔲 Payment webhook processing
- 🔲 Logistics/shipment preparation
- 🔲 Full end-to-end integration tests

## License

Private — all rights reserved.
