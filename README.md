# Commercial Bank AI Assistant

An enterprise AI-assistant proof of concept for evidence-grounded answers over internal
knowledge. The target system combines a FastAPI/Streamlit interface, a controlled Deep Agent
harness, a bounded LangGraph research workflow, hybrid Pinecone retrieval, role-aware tools,
session memory, and LangSmith observability.

> Current status: **Phase 0 complete — contracts and project foundations.** No application
> service is claimed as runnable until the Phase 1 walking skeleton is implemented.

## POC scope

The POC supports one fictional organization (`commercial-bank`), three hardcoded users and
roles, six read-only tools, and four document categories. It answers general knowledge
questions, performs bounded incident research and structured analysis, maintains context
within a conversation, and emits inspectable activity events. Authorization, access filters,
execution limits, citation validation, and rate limiting are deterministic platform controls.

### Included

- Streamed, multi-turn chat with a real-time agent activity feed
- Viewer, Analyst, and Administrator roles
- Policies, architecture documents, runbooks, and incident reports
- Dense + BM25 hybrid retrieval with metadata filtering and evidence attribution
- Knowledge search, structured analysis, and approved read-only MCP tools
- Session memory, prompt-injection controls, output validation, graceful degradation
- LangSmith traces and structured logs without hidden chain-of-thought

### Explicitly out of scope

- Production identity federation, an admin portal, and document-upload UI
- Cross-organization tenancy and unrestricted long-term memory
- Arbitrary Python, shell access, write-capable MCP tools, or autonomous transactions
- High availability, production data migration, and model/provider failover
- Customer-specific banking advice or actions

The frozen assumptions and boundaries are detailed in [docs/scope-and-assumptions.md](docs/scope-and-assumptions.md).

## Architecture

The root agent plans and delegates, while code—not prompts—owns identity, authorization,
retrieval scope, tool policy, budgets, and validation. Complex research runs in a compiled,
bounded LangGraph subgraph. See [docs/architecture.md](docs/architecture.md) and the
[architecture decisions](docs/adr/).

## Contracts and acceptance baseline

- API, SSE, response, citation, and error contracts: [docs/contracts.md](docs/contracts.md)
- Execution and rate limits: [config/defaults.yaml](config/defaults.yaml)
- Golden assessment scenarios: [data/golden_questions.json](data/golden_questions.json)
- Decision records: [docs/adr](docs/adr)

## Repository layout

```text
src/orysys_assistant/  application package and deterministic domain contracts
ui/                    Streamlit frontend (Phase 1)
mcp_server/            read-only mock enterprise MCP server (Phase 7)
skills/                reusable agent instructions (later phases)
data/                   sample documents and golden acceptance cases
config/                 non-secret policy and execution defaults
tests/                  unit, graph, integration, and end-to-end suites
docs/                   architecture, scope, contracts, security, and ADRs
```

## Development setup

The project targets Python 3.11–3.13 (3.12 recommended) and uses `uv.lock` for reproducible
installs.

```bash
cp .env.example .env
uv sync --frozen --dev
uv run pytest
uv run ruff check .
uv run mypy src
```

Do not commit `.env` or secrets. The Phase 1 service commands will be added with the walking
skeleton.

## Delivery roadmap

1. Phase 0 — freeze scope, contracts, architecture, dependencies, and golden scenarios
2. Phase 1 — FastAPI/Streamlit streaming walking skeleton
3. Phase 2 — authentication, authorization, tool gateway, and Redis rate limiting
4. Phase 3 — sample corpus, ingestion, and hybrid Pinecone retrieval
5. Phase 4 — root Deep Agent and specialized agents
6. Phase 5 — bounded recursive LangGraph research workflow
7. Phase 6+ — memory, MCP/analysis tools, hardening, observability, and deployment

The original assessment is preserved in [assignment.md](assignment.md); the working plan is
[lead_ai_assignment_phase_implementation_plan.md](lead_ai_assignment_phase_implementation_plan.md).

