# Commercial Bank AI Assistant

An enterprise AI-assistant proof of concept for evidence-grounded answers over internal
knowledge. The target system combines a FastAPI/Streamlit interface, one controlled production
LangGraph, a bounded research subgraph, hybrid Pinecone retrieval, role-aware tools,
session memory, and LangSmith observability.

> **Current status:** Phase 10 complete — model-backed or deterministic routing, bounded agent
> workflows, reranking, explicit preferences, durable approval records, observability, and hardened
> packaging are integrated.

## Problem statement

Employees need one conversational entry point across policies, architecture, runbooks, incidents,
product specifications, and meeting notes. A useful answer is not enough: the platform must prove
which authorized evidence supported it, preserve owned conversation context, constrain tools by
role, expose safe operational activity, and fail without fabricating policy or leaking data.

## POC scope

The POC supports one fictional organization (`commercial-bank`), four hardcoded users across three
roles, eight registered tools (six read-only MCP operations plus knowledge search and structured
analysis), and six document categories. It answers general knowledge questions, performs bounded
incident research and structured analysis, maintains context within a conversation, and emits
inspectable activity events. Authorization, access filters, execution limits, citation validation,
and rate limiting are deterministic platform controls.

### Included

- Streamed, multi-turn chat with a real-time agent activity feed
- Viewer, Analyst, and Administrator roles with four demo identities
- Policies, architecture documents, runbooks, incidents, product specifications, and meeting notes
- Dense + BM25 hybrid retrieval with metadata filtering, reranking, and evidence attribution
- Knowledge search, structured analysis, and approved read-only MCP tools
- Session memory, explicit long-term preferences, prompt-injection controls, output validation,
  and graceful degradation
- Failure containment, human four-eyes approval for the synthetic incident write, and an admin
  approval center in the Streamlit UI
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
bounded LangGraph subgraph. See [docs/architecture.md](docs/architecture.md), the
[documentation index](docs/README.md), and the [architecture decisions](docs/adr/).

## Contracts and acceptance baseline

- API, SSE, response, citation, and error contracts: [docs/contracts.md](docs/contracts.md)
- Execution and rate limits: environment-backed [settings](src/orysys_assistant/config.py)
- Golden assessment scenarios: [data/golden_questions.json](data/golden_questions.json)
- Decision records: [docs/adr](docs/adr)

## Repository layout

```text
src/orysys_assistant/  application package and deterministic domain contracts
ui/                    Streamlit frontend
mcp_server/            read-only mock enterprise MCP server
scripts/               ingestion, evaluation, smoke test, and delivery checks
docker/                container build definitions
data/                  sample documents, golden cases, and evaluation reports
tests/                 unit, graph, integration, and end-to-end suites
docs/                  architecture, scope, contracts, security, and ADRs
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

### Local development (without Docker)

For a single-process local run, use the in-memory adapters for retrieval, memory, MCP, and rate
limiting. The checked-in `.env.example` defaults to Redis for rate limiting; override that when
Redis is not running locally.

```powershell
Copy-Item .env.example .env
$env:MEMORY_BACKEND="memory"
$env:RATE_LIMIT_BACKEND="memory"
$env:MCP_BACKEND="memory"
$env:RETRIEVAL_BACKEND="memory"
uv run python -m uvicorn orysys_assistant.main:app --reload --port 8000
uv run python -m streamlit run ui/app.py
```

Then open `http://localhost:8501`. API documentation is available at
`http://localhost:8000/docs`. Sign in through the Streamlit UI with one of the demo credentials
below; the API exchanges username/password for an opaque bearer token via `POST /v1/auth/token`.

### Docker Compose

Alternatively, start the full stack from a clean environment:

```bash
docker compose up --build
```

Compose starts PostgreSQL, Redis, the stateless MCP server, API, and UI. PostgreSQL stores
owner-isolated conversation records, LangGraph checkpoints, explicit preferences, and approval
records. The in-memory adapters are limited to tests and explicit single-process development.

For the complete detached startup, ingestion verification, smoke test, Pinecone mode, and shutdown
commands, see [docs/deployment.md](docs/deployment.md).

## Environment variables

Copy `.env.example` and keep `.env` untracked. The principal controls are:

| Variable | Default | Purpose |
|---|---|---|
| `RETRIEVAL_BACKEND` | `memory` | deterministic local retrieval or `pinecone` |
| `MEMORY_BACKEND` | `memory` locally; `postgres` in Compose | conversation, checkpoint, and preference storage |
| `RATE_LIMIT_BACKEND` | `redis` | shared token-bucket adapter; use `memory` for local single-process dev |
| `MCP_BACKEND` | `memory` locally; `http` in Compose | enterprise-tool transport |
| `AGENT_MODEL` | `gpt-5-mini` | supervisor routing and optional answer synthesis model |
| `AGENT_SYNTHESIS_ENABLED` | `true` | enable model-backed answer synthesis when a key is present |
| `RETRIEVAL_RERANKING_ENABLED` | `true` | blend lexical reranking over hybrid candidates |
| `LANGSMITH_TRACING` | `false` | enable trace export when a key is present |
| `API_PORT`, `UI_PORT` | `8000`, `8501` | loopback Compose ports |
| `REQUEST_TIMEOUT_SECONDS` | `120` | overall request deadline |

When `OPENAI_API_KEY` is unset, the local deterministic profile uses `DeterministicIntentRouter`;
set the key to enable the model-backed supervisor and answer synthesis. Pinecone mode additionally
requires `PINECONE_API_KEY`, index/host configuration, and a matching embedding dimension. Compose
maps the four `AUTH_*_TOKEN` values to the demo bearer tokens used after login. All limits and
adapter variables are listed in [.env.example](.env.example).

Do not commit `.env` or secrets.

## Sample users

These credentials are fictional and exist only for the assessment POC:

| Role | Username | Password |
|---|---|---|
| Viewer | `viewer@commercialbank.test` | `ViewerDemo!2026` |
| Analyst | `analyst@commercialbank.test` | `AnalystDemo!2026` |
| Administrator | `admin@commercialbank.test` | `AdminDemo!2026` |
| Administrator (approver) | `approver@commercialbank.test` | `ApproverDemo!2026` |

The application authentication records store salted PBKDF2 digests rather than these published
demo passwords. Successful login returns an opaque bearer token whose identity and role are
resolved only by the backend. Use the approver account to approve or reject another administrator's
pending synthetic incident change.

## Example questions

- Viewer: “What does Commercial Bank's remote-work policy allow?”
- Analyst research: “Summarize payment-failure outages from the last year and identify recurring
  root causes.”
- Analyst analysis: “Show the distribution of retrieved incidents by document type.”
- Analyst MCP: “Who owns the Payments Gateway service?”
- Follow-up memory: “Does that remote-work rule apply during probation?”
- Administrator: “Explain the restricted fraud investigation playbook.”

## Current capabilities

| Area | Summary | Details |
|---|---|---|
| Streaming API | `POST /v1/chat/stream` emits `activity`, `answer_delta`, and terminal `final` events | [contracts.md](docs/contracts.md) |
| Security | Bearer auth, one authorization matrix, Tool Gateway, Redis rate limiting | [security.md](docs/security.md) |
| Corpus and retrieval | 48 synthetic Markdown documents, hybrid dense + BM25, optional reranking | [retrieval.md](docs/retrieval.md) |
| Agent orchestration | One LangGraph with model-backed or deterministic supervisor routing | [agents.md](docs/agents.md) |
| Research workflow | Bounded plan → worker fan-out → reduce → coverage → follow-up | [research-graph.md](docs/research-graph.md) |
| Memory and tools | PostgreSQL checkpoints, preferences API, analysis tool, six MCP reads | [memory-and-tools.md](docs/memory-and-tools.md) |
| Guardrails | Evidence ledger, citation validation, retries, dense-only and partial degradation | [guardrails-and-degradation.md](docs/guardrails-and-degradation.md) |
| Observability | Activity panel, trace correlation, golden evaluation runner | [observability-and-evaluation.md](docs/observability-and-evaluation.md) |
| Phase 10 extras | Reranking, preferences, failure circuit breaking, four-eyes approvals | [phase-10-bonus-features.md](docs/phase-10-bonus-features.md) |

Generate or verify the corpus with:

```bash
uv run python scripts/generate_sample_documents.py
uv run python scripts/ingest_sample_documents.py --backend memory
```

Eight demanding multi-source prompts and their expected evidence sets are provided in
[data/hard_research_questions.json](data/hard_research_questions.json).

To ingest into an existing Pinecone dense index, configure `PINECONE_API_KEY`,
`PINECONE_INDEX` (or preferably `PINECONE_HOST`), `OPENAI_API_KEY`, the embedding model and
dimension, then run:

```bash
uv run python scripts/ingest_sample_documents.py --backend pinecone
```

### LangSmith tracing

Set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, and optionally `LANGSMITH_PROJECT` in `.env`.
Root routing, retrieval, and each delegation boundary are decorated as LangSmith runs. Tracing is
disabled safely when no key/configuration is supplied.

## Agent and RLM design

The API routes focused knowledge directly and delegates research, controlled analysis, or approved
enterprise reads to exactly three specialists. The simplified Recursive Language Model path is a
compiled LangGraph that plans targeted tasks, fans out bounded workers, reduces evidence, checks
coverage, and permits limited follow-up recursion. See [docs/agents.md](docs/agents.md) and
[docs/research-graph.md](docs/research-graph.md).

## Retrieval, security, failure handling, and memory

- Retrieval: dense plus BM25 fusion, optional reranking, conservative relevance filtering, trusted
  namespace/metadata scope, deterministic attribution, and a Pinecone adapter. See
  [docs/retrieval.md](docs/retrieval.md).
- Security: backend-owned identity/scope, one authorization matrix, typed Tool Gateway, content
  quarantine, citation ledger, rate limiting, and safe activity metadata. See
  [docs/security.md](docs/security.md).
- Failure handling: bounded retries/deadlines, worker isolation, dense-only degradation, document
  fallback, and insufficient-evidence responses. See
  [docs/guardrails-and-degradation.md](docs/guardrails-and-degradation.md).
- Memory: owner-isolated PostgreSQL turns, explicit preferences, and strict LangGraph checkpoints
  with bounded summaries. See [docs/memory-and-tools.md](docs/memory-and-tools.md).

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
Current golden results report 100% route accuracy, citation validity, groundedness, permission
accuracy, expected completion status, and partial/degraded answer clarity, with zero
unauthorized-evidence exposure.

A step-by-step evaluator walkthrough is available in [docs/demo-script.md](docs/demo-script.md).
Manual role and capability cases are in
[docs/role-capability-question-set.md](docs/role-capability-question-set.md).

## Assumptions, known limitations, and future improvements

The default is an offline deterministic assessment path; Pinecone, hosted model synthesis, and
LangSmith require external credentials. Authentication is a hardcoded POC fixture, Compose is
single-host, feedback is accepted by the API but not persisted, and the assistant never performs
real banking transactions. The rationale, limitations, and production follow-ups are documented in
[docs/assumptions-and-tradeoffs.md](docs/assumptions-and-tradeoffs.md).

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
11. Phase 10 — complete: reranking, explicit preferences, failure circuit breaking, and durable
    four-eyes approval for the demo incident write

The original assessment is preserved in [assignment.md](assignment.md); the working plan is
[lead_ai_assignment_phase_implementation_plan.md](lead_ai_assignment_phase_implementation_plan.md).

## Documentation

See [docs/README.md](docs/README.md) for the full documentation index.
