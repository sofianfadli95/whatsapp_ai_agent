---
inclusion: always
---

# Tech Stack

## Project Context

This project is a WhatsApp-based AI commerce agent for online sellers. The system receives WhatsApp messages, runs an AI sales/customer-service agent, answers product questions, manages carts and orders, generates payment links, receives payment callbacks, and prepares shipment after verified payment success.

The architecture should prioritize:

- Event-driven processing
- Safe transactional workflows
- Persistent conversation state
- Deterministic backend logic for commerce actions
- Modular tools that can later be exposed through MCP servers
- Cloud Run deployment readiness

## Primary Stack

### Language

Use **Python 3.11+** as the primary language.

Reasons:

- Strong LangChain and LangGraph ecosystem support
- Good FastAPI support for webhooks and APIs
- Mature async HTTP tooling
- Easy integration with PostgreSQL, Cloud Run, Cloud Tasks, and AI SDKs

### Web Framework

Use **FastAPI** for HTTP APIs and webhooks.

Primary responsibilities:

- WhatsApp webhook receiver
- Payment webhook receiver
- Logistics webhook receiver
- Internal worker endpoints
- Health check endpoint
- Admin/internal debugging endpoints when needed

Recommended server:

- `uvicorn` for local development
- `gunicorn` with Uvicorn workers may be added later if needed

### AI Orchestration

Use **LangGraph** as the main agent orchestration framework.

LangGraph should manage:

- Conversation state
- Conditional routing
- Multi-turn checkout flows
- Tool-calling workflows
- Human escalation paths
- Long-running workflows that resume after external events
- Checkpointing conversation state to persistent storage

Use **LangChain** for:

- Chat model integration
- Prompt templates
- Tool definitions
- Structured output parsing
- Product retriever integration
- RAG chain components

### MCP

Use **MCP** as the future tool boundary for business capabilities.

Initial MVP may implement tools directly as Python services or LangChain tools. Once the core flow is stable, refactor tools into MCP servers.

Planned MCP servers:

- `catalog-mcp-service`
- `order-mcp-service`
- `payment-mcp-service`
- `logistics-mcp-service`
- `human-support-mcp-service`

Each MCP tool should expose a clear schema, validate inputs, and return structured outputs.

### LLM Provider

The LLM provider should be configurable by environment variable.

Expected supported providers:

- OpenAI
- Anthropic
- Google Vertex AI / Gemini

Do not hardcode provider-specific logic deep inside the agent graph. Wrap provider setup behind a model factory.

Example environment variables:

```bash
LLM_PROVIDER=openai
LLM_MODEL=gpt-4.1-mini
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...