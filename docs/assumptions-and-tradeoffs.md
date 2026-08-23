# Assumptions, Trade-offs, and Known Limitations

## POC assumptions

- Commercial Bank, all users, credentials, documents, enterprise records, and incidents are
  fictional.
- One organization and namespace are sufficient to demonstrate trusted-scope enforcement.
- English text and Markdown source documents represent the assessment corpus.
- Enterprise tools are reads, except the explicitly bounded, synthetic `modify_incident` approval demo.
- PostgreSQL, Redis, Pinecone, OpenAI, and LangSmith are independently operated dependencies in a
  production topology.

## Architecture trade-offs

### Model routing with deterministic enforcement

There is no classifier. The root is an agent whose only tools are the four specialists, so routing is
its choice of delegation tool and can be revised after seeing a result. Tool surfaces, identity,
authorization, scope, budgets, citation resolution, and validation remain deterministic code, and the
route and status the turn reports are rebuilt from the consultations that actually produced evidence
rather than from anything the model asserts. An OpenAI credential is required, because the root and
every specialist below it is a model-driven loop. Tests inject a scripted chat model to make the
model's turns deterministic while still exercising the real loop, middleware, and gateway.

### Simplified RLM

The research specialist implements Recursive Language Model concepts on the Deep Agents harness: it
decomposes the objective with `write_todos`, issues parallel `knowledge_search` calls, offloads long
passages to an in-state virtual filesystem, and re-plans against what it found. Recursion is the
agent revisiting its own plan rather than a fixed-depth follow-up loop.

Code still owns the parts that must not vary: tool visibility, RBAC, tool and model call ceilings
enforced as middleware, the overall deadline, and the reconstruction of evidence and citations from
retrievals that actually executed. A finding citing an evidence identifier the model invented is
dropped before it can become a citation. Analysis operations remain a fixed set of audited
aggregations rather than model-generated Python, so adaptive planning arrives without a
code-execution surface.

### Offline retrieval versus Pinecone

The checked-in default uses deterministic hash embeddings plus BM25 so tests and demos require no
credentials. The same service contract has a Pinecone dense/sparse adapter with namespace and
metadata filters. Offline scores are not a proxy for production semantic quality; the relevance
floor is deliberately conservative and may return insufficient evidence rather than weak matches.

### Memory scope

Conversation turns, summaries, evidence IDs, and LangGraph checkpoints persist in PostgreSQL.
Explicit user preferences are stored separately and can be listed or deleted. There is no vectorized
long-term memory or retention scheduler; these require governance decisions outside the POC.

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
- Replace the synthetic incident-write handler with an idempotent enterprise integration before
  enabling any real administrative action.
- Persist feedback and connect it to regression datasets and production-quality dashboards.
