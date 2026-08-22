# Assessment Demo Script

Target duration: 15–20 minutes. Start from a healthy Compose stack and keep the Streamlit activity
panel visible. If LangSmith is configured, open the matching project in a separate browser tab.

## 1. Problem and architecture

Explain that the system answers internal knowledge questions while enforcing identity, document
scope, tool permissions, evidence grounding, and failure containment. Show `docs/architecture.md`.
Call out one root harness, three specialists, the explicit recursive research graph, and security
services outside agent prompts.

## 2. Simple grounded retrieval

Sign in as Viewer and ask:

> What does Commercial Bank's remote-work policy allow?

Show streaming, `knowledge_search`, hybrid candidate/selection counts, validation completion, the
trace ID, citation `[1]`, and the evidence-source drawer.

## 3. Recursive research workflow

Sign in as Analyst and ask:

> Summarize payment-failure outages from the last year and identify recurring root causes.

Show planner tasks, parallel workers, reducer, coverage check, bounded follow-up behavior, evidence
deduplication, partial-state handling, and the research trace tree.

## 4. Conversation memory

In the Viewer conversation, ask:

> Does that remote-work rule apply during probation?

Show the same conversation ID, memory-loaded event, contextual response, and memory-updated event.
Explain that PostgreSQL stores bounded messages/summaries and evidence IDs, not full documents or
hidden reasoning.

## 5. RBAC and enterprise tools

As Viewer ask for an employee-directory record and show the denied tool event. Repeat an approved
service-ownership question as Analyst:

> Who owns the Payments Gateway service?

Show the read-only MCP call. Explain that Administrator additionally receives restricted-document
scope, while no role receives write tools.

## 6. Security controls

- Show `incident-injection-001.md` and explain evidence wrapping/quarantine.
- Submit a role/namespace field through an API client and show HTTP 400.
- Run the fabricated-citation golden case and show one repair/revalidation followed by
  `insufficient_evidence` with no fabricated citation returned.
- Point to the Tool Gateway and activity metadata allowlist as deterministic boundaries.

## 7. Failure handling

Run `uv run python scripts/run_golden_evaluation.py`. Highlight the reproducible MCP timeout,
retrieval outage, worker isolation, rate-limit, and citation-failure cases. Open
`data/golden_evaluation_report.json` and show zero unauthorized evidence plus 100% citation validity.

## 8. Observability and code quality

Correlate the UI trace ID with structured API logs and LangSmith. Show root routing, subagent/graph
nodes, authorization, tool, retrieval, and validator runs in one trace tree. Then run:

```powershell
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/check_public_readiness.py
```

## 9. Packaging and future work

Show the five healthy Compose services, non-root/read-only container settings, `.env.example`, CI,
ADRs, and `docs/assumptions-and-tradeoffs.md`. Close with provider-backed synthesis, enterprise SSO,
managed infrastructure, ingestion orchestration, human approval for future writes, and feedback-led
continuous evaluation.
