---
inclusion: always
---

# Project Structure

## Purpose

This document defines the expected project structure for the WhatsApp Sales Agent project.

The system is a WhatsApp-based AI commerce agent that uses FastAPI, LangGraph, LangChain, PostgreSQL, pgvector, Cloud Tasks, Cloud Run, and MCP-ready tool boundaries.

The codebase should be organized around clear separation of concerns:

- API routes receive and validate external requests
- Services contain business logic
- Repositories handle database access
- Agent modules contain LangGraph workflows
- Tools expose controlled capabilities to the agent
- MCP servers are optional future boundaries
- Workers process asynchronous tasks
- Tests validate commerce, agent, webhook, and integration flows

## Root Directory Layout

```txt
ai-commerce-agent/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── constants.py
│   ├── api/
│   ├── agent/
│   ├── tools/
│   ├── services/
│   ├── repositories/
│   ├── db/
│   ├── vectorstore/
│   ├── workers/
│   ├── schemas/
│   ├── observability/
│   └── utils/
├── mcp_servers/
├── tests/
├── scripts/
├── docs/
├── infra/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── uv.lock
├── .env.example
├── .gitignore
└── README.md