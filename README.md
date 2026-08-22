# Commercial Bank AI Assistant

An enterprise AI-assistant proof of concept for evidence-grounded answers over internal
knowledge. The target system combines a FastAPI/Streamlit interface, a controlled Deep Agent
harness, a bounded LangGraph research workflow, hybrid Pinecone retrieval, role-aware tools,
session memory, and LangSmith observability.

> Current status: **Phase 3 complete — authenticated walking skeleton plus a tested hybrid
> evidence layer.** The chat agent remains a deterministic mock until orchestration is introduced
> in Phase 4.

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

## Run the walking skeleton

The project targets Python 3.11–3.13 (3.12 recommended) and uses `uv.lock` for reproducible
installs.

```bash
cp .env.example .env
uv sync --frozen --dev
uv run pytest
uv run ruff check .
uv run mypy src
```

Start the API and UI in separate terminals:

```powershell
$env:RATE_LIMIT_BACKEND="memory" # local single-process development only
uv run python -m uvicorn orysys_assistant.main:app --reload --port 8000
uv run python -m streamlit run ui/app.py
```

Then open `http://localhost:8501`. API documentation is available at
`http://localhost:8000/docs`.

Alternatively, start both services from a clean environment:

```bash
docker compose up --build
```

Compose starts Redis and uses the shared atomic token bucket. The memory backend is deliberately
limited to tests and explicit single-process development.

### Demo identities

These credentials are fictional and exist only for the assessment POC:

| Role | Username | Password |
|---|---|---|
| Viewer | `viewer@commercialbank.test` | `ViewerDemo!2026` |
| Analyst | `analyst@commercialbank.test` | `AnalystDemo!2026` |
| Administrator | `admin@commercialbank.test` | `AdminDemo!2026` |

The application authentication records store salted PBKDF2 digests rather than these published
demo passwords. Successful login returns an opaque bearer token whose identity and role are
resolved only by the backend.

Do not commit `.env` or secrets.

### LangSmith tracing

Set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, and optionally `LANGSMITH_PROJECT` in `.env`.
Each chat request creates a `phase1-mock-agent` run tagged `phase-1` and `walking-skeleton`.
Tracing is disabled safely when no key/configuration is supplied.

### Phase 1 behavior

- `POST /v1/chat/stream` emits separate `activity`, `answer_delta`, and terminal `final` events.
- `GET /health/live` and `GET /health/ready` expose process health.
- Conversation and feedback endpoints expose their frozen contracts but explicitly report that
  persistence is unavailable until the memory phase.
- A server-generated request ID is returned in `X-Request-ID` and propagated to events/logs.
- Client disconnects cancel the stream. Errors use the common Phase 0 envelope.

### Phase 2 security boundary

- Every `/v1` resource except token issuance requires a bearer identity.
- One authorization policy owns all role-to-capability decisions.
- Organization, namespace, department, access levels, role, and user ID come from trusted
  backend context and cannot be supplied through prompts or tool parameters.
- All tool execution must pass through the typed gateway for allowlist, RBAC, reserved-field
  rejection, schema validation, timeout, result-size limit, and audit logging.
- Redis executes the per-user token bucket atomically, so limits are shared by API instances.
- Authentication and rate-limit denials happen before the mock agent and return consistent
  `401`, `403`, or `429` envelopes.

### Phase 3 corpus and retrieval

The repository contains 36 deterministic fictional documents across policies, architecture,
runbooks, incidents, product specifications, and meeting notes. Generate or verify them with:

```bash
uv run python scripts/generate_sample_documents.py
uv run python scripts/ingest_sample_documents.py --backend memory
```

The memory command executes parsing, section-aware chunking, deterministic IDs, dense encoding,
BM25 sparse encoding, idempotent upsert, stale-vector cleanup, and manifest generation without
external credentials. It is the test/evaluation adapter, not the deployment vector database.

To ingest into an existing Pinecone dense index, configure `PINECONE_API_KEY`,
`PINECONE_INDEX` (or preferably `PINECONE_HOST`), `OPENAI_API_KEY`, the embedding model and
dimension, then run:

```bash
uv run python scripts/ingest_sample_documents.py --backend pinecone
```

The Pinecone index dimension must match `EMBEDDING_DIMENSION`. Retrieval executes dense and sparse
queries asynchronously inside the trusted organization namespace, fuses normalized scores using
the configured 0.65/0.35 weights, and returns attributed evidence. See
[docs/retrieval.md](docs/retrieval.md).

## Delivery roadmap

1. Phase 0 — complete: scope, contracts, architecture, dependencies, and golden scenarios
2. Phase 1 — complete: FastAPI/Streamlit streaming walking skeleton
3. Phase 2 — complete: authentication, authorization, tool gateway, and Redis rate limiting
4. Phase 3 — complete: sample corpus, ingestion, and hybrid Pinecone retrieval
5. Phase 4 — root Deep Agent and specialized agents
6. Phase 5 — bounded recursive LangGraph research workflow
7. Phase 6+ — memory, MCP/analysis tools, hardening, observability, and deployment

The original assessment is preserved in [assignment.md](assignment.md); the working plan is
[lead_ai_assignment_phase_implementation_plan.md](lead_ai_assignment_phase_implementation_plan.md).
