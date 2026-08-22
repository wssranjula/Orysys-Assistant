# Commercial Bank AI Assistant

An enterprise AI-assistant proof of concept for evidence-grounded answers over internal
knowledge. The target system combines a FastAPI/Streamlit interface, one controlled production
LangGraph, a bounded research subgraph, hybrid Pinecone retrieval, role-aware tools,
session memory, and LangSmith observability.

> Current status: **Phase 8 complete — the safe real-time activity panel, correlated trace tree,
> golden evaluation runner, stored metrics, and role-complete end-to-end suite are integrated.**

## Problem statement

Employees need one conversational entry point across policies, architecture, runbooks, incidents,
product specifications, and meeting notes. A useful answer is not enough: the platform must prove
which authorized evidence supported it, preserve owned conversation context, constrain tools by
role, expose safe operational activity, and fail without fabricating policy or leaking data.

## POC scope

The POC supports one fictional organization (`commercial-bank`), three hardcoded users and
roles, six read-only tools, and six document categories. It answers general knowledge
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
mcp_server/            read-only mock enterprise MCP server
data/                   sample documents and golden acceptance cases
config/                 non-secret policy and execution defaults
tests/                  unit, graph, integration, and end-to-end suites
docs/                   architecture, scope, contracts, security, and ADRs
```

## Quick start

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

Compose starts PostgreSQL, Redis, the stateless MCP server, API, and UI. PostgreSQL stores
owner-isolated conversation records and LangGraph checkpoints. The in-memory adapters are limited
to tests and explicit single-process development.

For the complete detached startup, ingestion verification, smoke test, Pinecone mode, and shutdown
commands, see [docs/deployment.md](docs/deployment.md).

## Environment variables

Copy `.env.example` and keep `.env` untracked. The principal controls are:

| Variable | Default | Purpose |
|---|---|---|
| `RETRIEVAL_BACKEND` | `memory` | deterministic local retrieval or `pinecone` |
| `MEMORY_BACKEND` | `memory` locally; `postgres` in Compose | conversation/checkpoint storage |
| `RATE_LIMIT_BACKEND` | `redis` | shared token-bucket adapter |
| `MCP_BACKEND` | `memory` locally; `http` in Compose | enterprise-tool transport |
| `AGENT_MODEL` | `gpt-5-mini` | supervisor routing and optional answer synthesis model |
| `LANGSMITH_TRACING` | `false` | enable trace export when a key is present |
| `API_PORT`, `UI_PORT` | `8000`, `8501` | loopback Compose ports |
| `REQUEST_TIMEOUT_SECONDS` | `120` | overall request deadline |

The supervisor agent requires `OPENAI_API_KEY`; there is no keyword-routing fallback. Pinecone mode
additionally requires `PINECONE_API_KEY`, index/host configuration, and a matching embedding
dimension. All limits and adapter variables are listed
in [.env.example](.env.example); non-secret policy defaults are in
[config/defaults.yaml](config/defaults.yaml).

## Sample users

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

## Example questions

- Viewer: “What does Commercial Bank's remote-work policy allow?”
- Analyst research: “Summarize payment-failure outages from the last year and identify recurring
  root causes.”
- Analyst analysis: “Show the distribution of retrieved incidents by document type.”
- Analyst MCP: “Who owns the Payments Gateway service?”
- Follow-up memory: “Does that remote-work rule apply during probation?”
- Administrator: “Explain the restricted fraud investigation playbook.”

### LangSmith tracing

Set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, and optionally `LANGSMITH_PROJECT` in `.env`.
Root routing, retrieval, and each delegation boundary are decorated as LangSmith runs. Tracing is
disabled safely when no key/configuration is supplied.

### Phase 1 behavior

- `POST /v1/chat/stream` emits separate `activity`, `answer_delta`, and terminal `final` events.
- `GET /health/live` and `GET /health/ready` expose process health.
- Conversation endpoints expose owner-isolated persisted turns; feedback retains its placeholder
  acknowledgement until a later phase.
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
- Authentication and rate-limit denials happen before the root agent and return consistent
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

### Phase 4 agent orchestration

The API now uses one compiled, auditable LangGraph with an LLM supervisor routing node. The
supervisor returns a strict `RouteDecision` containing only one allowed route; code-controlled
conditional edges perform the actual delegation, and the application generates the user-safe plan
summary for that route.
Focused questions use authorized knowledge search directly; multi-document research, structured
analysis, and enterprise lookups delegate to exactly three static specialists. Every specialist
has a small code-enforced tool allowlist, while the central gateway continues to enforce RBAC,
trusted scope, schemas, timeouts, and audit logging.

The four operational branches and one no-tool `out_of_scope` branch converge on one synthesis node.
Out-of-scope requests receive a fixed explanation of the assistant's approved duties. When enabled,
the synthesis node uses LangChain
structured output to produce grounded prose; deterministic synthesis can still be used for answers,
but production routing always requires the model-backed supervisor.
There is no second, unused agent harness. See [docs/agents.md](docs/agents.md).

### Phase 5 bounded research workflow

Complex research requests now enter a compiled LangGraph subgraph. It normalizes trusted scope,
creates up to four independent tasks, fans workers out with LangGraph `Send` through the Tool Gateway,
deduplicates evidence and claims, checks coverage, and performs at most two bounded follow-up rounds.
Code-enforced limits cover parallel workers, recursion depth, total tool calls, evidence per worker,
worker deadlines, and the overall deadline.

Worker failures are converted to structured warnings while other workers continue. Exhausted budgets
produce a `partial` response with unresolved coverage questions instead of an unbounded retry or a
fabricated complete answer. Research-node transitions are streamed to the existing UI activity feed,
and graph nodes plus workers appear in LangSmith. See
[docs/research-graph.md](docs/research-graph.md).

### Phase 6 memory and enterprise tools

Each conversation is owned by the authenticated user and keyed by user plus conversation ID.
The root LangGraph checkpointer owns execution-time message history using the server-derived user and
conversation ID. The owner-isolated conversation repository remains a compact API read model with
recent messages, a bounded display summary, and evidence IDs—not full retrieved documents,
credentials, raw MCP responses, or hidden reasoning.

The Analysis specialist now invokes a typed controlled tool supporting only `count_by`, `group_by`,
`trend_by_date`, `top_values`, and `percentage`. The Enterprise specialist can invoke six read-only
employee, service, and incident operations through an MCP adapter. Both remain behind role checks,
schema validation, timeouts, response-size limits, audit logs, and visible tool activity. See
[docs/memory-and-tools.md](docs/memory-and-tools.md).

### Phase 7 guardrails and graceful degradation

Every request is validated before state or agent work begins. Retrieved text is explicitly wrapped
as untrusted evidence and instruction-like fragments are quarantined. Grounded responses carry an
internal current-request evidence ledger; citations, authorization scope, inline markers, and
response policy are validated before the answer is persisted or streamed. A single constrained
repair can restore missing markers, while fabricated citations fail closed to
`insufficient_evidence`.

Temporary retrieval and MCP failures use bounded retries. Sparse failure uses labeled dense-only
results, MCP failure falls back to available authorized documents, research workers remain isolated,
and the overall request deadline cancels outstanding work. See
[docs/guardrails-and-degradation.md](docs/guardrails-and-degradation.md).

### Phase 8 activity, observability, and evaluation

The Streamlit activity panel now shows the current agent and graph node, a safe plan summary, active
tool, retrieval mode and filters, candidate and evidence counts, memory/validation state, degraded
mode, and one request trace ID. Its API-side metadata allowlist prevents prompts, credentials, raw
MCP responses, namespaces, and document bodies from reaching the browser. Citation details appear
in a source-only evidence drawer.

One trace context correlates the root agent, specialists, graph workers, authorization, tools,
retrieval, and validation. The offline golden runner executes all ten frozen scenarios with
repeatable fault injection and stores its machine-readable report. Current results are 100% for
route accuracy, citation validity, groundedness, permission accuracy, expected completion status,
and degraded-answer clarity, with zero unauthorized-evidence exposure. See
[docs/observability-and-evaluation.md](docs/observability-and-evaluation.md).

## Agent and RLM design

The API routes focused knowledge directly and delegates research, controlled analysis, or approved
enterprise reads to exactly three specialists. The simplified Recursive Language Model path is a
compiled LangGraph that plans targeted tasks, fans out bounded workers, reduces evidence, checks
coverage, and permits limited follow-up recursion. See [docs/agents.md](docs/agents.md) and
[docs/research-graph.md](docs/research-graph.md).

## Retrieval, security, failure handling, and memory

- Retrieval: dense plus BM25 fusion, conservative relevance filtering, trusted namespace/metadata
  scope, deterministic attribution, and a Pinecone adapter. See [docs/retrieval.md](docs/retrieval.md).
- Security: backend-owned identity/scope, one authorization matrix, typed Tool Gateway, content
  quarantine, citation ledger, rate limiting, and safe activity metadata. See
  [docs/security.md](docs/security.md).
- Failure handling: bounded retries/deadlines, worker isolation, dense-only degradation, document
  fallback, and insufficient-evidence responses. See
  [docs/guardrails-and-degradation.md](docs/guardrails-and-degradation.md).
- Memory: owner-isolated PostgreSQL turns and strict LangGraph checkpoints with bounded summaries.
  See [docs/memory-and-tools.md](docs/memory-and-tools.md).

## Observability and testing

Every request has one trace ID across SSE, structured logs, and the LangSmith context. The activity
panel displays safe agent/node/tool/retrieval/memory/validation summaries. The test pyramid covers
unit, graph, integration, failure injection, golden evaluation, and API-to-agent end-to-end paths
for all three roles.

```powershell
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/run_golden_evaluation.py
uv run python scripts/check_public_readiness.py
```

The stored report is [data/golden_evaluation_report.json](data/golden_evaluation_report.json).

## Assumptions, known limitations, and future improvements

The default is an offline deterministic assessment path; Pinecone, hosted model synthesis, and
LangSmith require external credentials. Authentication is a hardcoded POC fixture, Compose is
single-host, feedback is not persisted, and the assistant never performs transactions. The
rationale, limitations, and production follow-ups are documented in
[docs/assumptions-and-tradeoffs.md](docs/assumptions-and-tradeoffs.md). A step-by-step evaluator
walkthrough is available in [docs/demo-script.md](docs/demo-script.md).

## Delivery roadmap

1. Phase 0 — complete: scope, contracts, architecture, dependencies, and golden scenarios
2. Phase 1 — complete: FastAPI/Streamlit streaming walking skeleton
3. Phase 2 — complete: authentication, authorization, tool gateway, and Redis rate limiting
4. Phase 3 — complete: sample corpus, ingestion, and hybrid Pinecone retrieval
5. Phase 4 — complete: controlled root agent, three specialists, skills, and delegation traces
6. Phase 5 — complete: bounded concurrent research graph with follow-up recursion
7. Phase 6 — complete: owner-isolated memory, checkpoints, controlled analysis, and MCP tools
8. Phase 7 — complete: guardrails, evidence-ledger validation, retries, and safe degradation
9. Phase 8 — complete: activity UX, correlated traces, golden evaluation, and role E2E tests
10. Phase 9 — complete: hardened Compose packaging, delivery checks, CI, and assessment docs

The original assessment is preserved in [assignment.md](assignment.md); the working plan is
[lead_ai_assignment_phase_implementation_plan.md](lead_ai_assignment_phase_implementation_plan.md).
