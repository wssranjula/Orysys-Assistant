# Assumptions, Trade-offs, and Known Limitations

## POC assumptions

- Commercial Bank, all users, credentials, documents, enterprise records, and incidents are
  fictional.
- One organization and namespace are sufficient to demonstrate trusted-scope enforcement.
- English text and Markdown source documents represent the assessment corpus.
- All enterprise tools are reads; transaction and approval workflows are intentionally excluded.
- PostgreSQL, Redis, Pinecone, OpenAI, and LangSmith are independently operated dependencies in a
  production topology.

## Architecture trade-offs

### Deterministic control versus model autonomy

The API uses deterministic intent routing and typed specialists for reproducibility, security, and
offline assessment. A provider-backed Deep Agents factory is included and compiles with the same
restricted tool surfaces, but the default request path does not require or invoke a hosted chat
model. This makes every golden failure reproducible and keeps identity, authorization, scope,
budgets, and validation out of model prompts. A production version would insert model synthesis
behind these existing boundaries and evaluate model-specific groundedness.

### Simplified RLM

The research specialist implements Recursive Language Model concepts as an explicit LangGraph:
plan, bounded parallel workers, targeted retrieval, reduction, coverage checking, limited follow-up
recursion, and final aggregation. Plans and analysis operations are code-generated rather than
arbitrary model-generated Python. This gives the evaluator the recursion and state-management
behavior without introducing a code-execution surface.

### Offline retrieval versus Pinecone

The checked-in default uses deterministic hash embeddings plus BM25 so tests and demos require no
credentials. The same service contract has a Pinecone dense/sparse adapter with namespace and
metadata filters. Offline scores are not a proxy for production semantic quality; the relevance
floor is deliberately conservative and may return insufficient evidence rather than weak matches.

### Memory scope

Conversation turns, summaries, evidence IDs, and LangGraph checkpoints persist in PostgreSQL.
There is no cross-conversation personalization, vectorized long-term memory, retention scheduler,
or user-facing deletion workflow. These require governance decisions outside the POC.

### Authentication

Hardcoded users and opaque bearer tokens satisfy the assessment role model. They do not provide
federation, token rotation/revocation, MFA, lifecycle management, or a secrets vault. Production
deployment should use the organization's identity provider and short-lived audience-bound tokens.

## Known limitations

- LangSmith traces are available only when explicitly enabled with a valid key.
- Pinecone integration requires a pre-created compatible index and external credentials.
- Readiness verifies the configured runtime and rate limiter; it does not continuously probe every
  optional external provider after startup.
- Sparse encoding is derived from the POC corpus and must be rebuilt when the corpus changes.
- Streamlit session state is browser-local; PostgreSQL remains the source for owned conversation
  recovery.
- Feedback is accepted through the frozen API placeholder but is not yet persisted or used to train
  evaluators.
- The system does not perform banking transactions or give account-specific financial advice.
- Compose is a single-host assessment deployment, not a high-availability production topology.

## Production follow-ups

- Add enterprise SSO, secrets management, key rotation, audit retention, and data-loss prevention.
- Introduce provider-backed synthesis, model fallback, prompt/version registries, and online evals.
- Use managed PostgreSQL/Redis, autoscaled API/UI workloads, encrypted service identity, and
  centralized telemetry.
- Add ingestion orchestration, document lifecycle events, index migration, reranking, and freshness
  monitoring.
- Add human approval for any future write-capable or high-impact workflow.
- Persist feedback and connect it to regression datasets and production-quality dashboards.
