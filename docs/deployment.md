# Deployment and Operations

## Prerequisites

- Docker Engine with Compose v2
- 4 GB or more available memory
- ports 8000 and 8501 available on loopback
- optional Pinecone, OpenAI, and LangSmith credentials

All repository data is synthetic. The default Compose profile uses local deterministic retrieval,
PostgreSQL conversation/checkpoint storage, Redis rate limiting, and the read-only mock MCP server.
It does not require cloud credentials.

## Clean startup

```powershell
Copy-Item .env.example .env
docker compose up --build --detach --wait
docker compose exec api python -m scripts.ingest_sample_documents --backend memory
uv run python scripts/smoke_test.py
```

On macOS/Linux, use `cp .env.example .env`. Replace the three bearer-token placeholders and the
database password in any shared environment. The memory ingestion command verifies the full
parsing/chunking/embedding pipeline; each API process also builds its deterministic in-memory index
at startup.

Expected endpoints:

| Surface | URL |
|---|---|
| Streamlit | `http://localhost:8501` |
| FastAPI OpenAPI | `http://localhost:8000/docs` |
| Liveness | `http://localhost:8000/health/live` |
| Readiness | `http://localhost:8000/health/ready` |

Compose binds public ports to `127.0.0.1` only. Set `API_PORT` or `UI_PORT` in `.env` to avoid a
local collision.

## Pinecone deployment mode

Set `RETRIEVAL_BACKEND=pinecone`, `PINECONE_API_KEY`, `PINECONE_INDEX` or `PINECONE_HOST`,
`OPENAI_API_KEY`, and an embedding dimension matching the existing Pinecone index. Then run:

```powershell
docker compose build api
docker compose run --rm --no-deps api python -m scripts.ingest_sample_documents --backend pinecone
docker compose up --detach --wait
```

The ingestion manifest is written to the `runtime-data` volume. The API prefers this runtime
manifest and falls back to the checked-in POC manifest. Pinecone and the model/embedding provider
remain external managed services; they are not emulated by Compose.

## Service topology and hardening

| Service | State | Exposure |
|---|---|---|
| `ui` | stateless | loopback port 8501 |
| `api` | stateless process; runtime manifest volume | loopback port 8000 |
| `mcp-server` | stateless synthetic records | backend network only |
| `postgres` | conversation and checkpoint volume | backend network only |
| `redis` | ephemeral token buckets | backend network only |

Application containers use UID 10001, `no-new-privileges`, read-only root filesystems, small tmpfs
mounts, and explicit health checks. PostgreSQL stores durable application state; the second named
volume stores only the reproducible ingestion manifest. Redis persistence is deliberately disabled
because token buckets can expire and rebuild.

## Operations

```powershell
docker compose ps
docker compose logs --follow api ui mcp-server
docker compose exec api python -m scripts.run_golden_evaluation --output /app/.data/golden_evaluation_report.json
docker compose down
```

`docker compose down` preserves named volumes. To intentionally remove synthetic local state, use
`docker compose down --volumes`; this deletes PostgreSQL conversations/checkpoints and the runtime
ingestion manifest.

## Delivery verification

Before publishing or handing off:

```powershell
uv sync --frozen --dev
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/check_public_readiness.py
docker compose config --quiet
docker compose build
```

CI executes the same quality, readiness, Compose validation, and image-build checks. Repository
visibility remains a hosting-platform setting and is never changed by a local script.
