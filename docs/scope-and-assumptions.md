# Frozen POC Scope and Assumptions

Status: accepted for Phase 0. Changes require a new ADR or an amendment to an existing ADR.

## Business boundary

- The assistant represents the fictional **Commercial Bank**.
- There is one organization and one Pinecone namespace: `commercial-bank`.
- The corpus is synthetic and contains no real customer, employee, or bank data.
- The assistant is informational and does not perform banking transactions or provide account-specific
  or unsupported financial advice. Its only write demonstration is a schema-bound synthetic incident
  change, which requires a separate administrator's explicit approval.
- English is the only supported language in the POC.

## Users and roles

Authentication uses opaque bearer tokens mapped server-side to these fixtures. The client may
never submit or override `user_id`, `role`, `namespace`, departments, or access levels.

| User ID | Display name | Role | Department |
|---|---|---|---|
| `user-viewer-01` | Vina Perera | Viewer | Retail Banking |
| `user-analyst-01` | Arun Silva | Analyst | Payments |
| `user-admin-01` | Maya Fernando | Administrator | Technology |
| `user-admin-approver-01` | Nimal Jayasinghe | Administrator | Risk |

| Capability | Viewer | Analyst | Administrator |
|---|---:|---:|---:|
| Chat and knowledge search | Yes | Yes | Yes |
| Structured analysis | No | Yes | Yes |
| Employee/service/incident MCP reads | No | Yes | Yes |
| Request synthetic incident change | No | No | Yes |
| Approve own request | No | No | No |
| Approve another administrator's request | No | No | Yes |

## Documents and access

Supported types are `policy`, `architecture`, `runbook`, `incident`, `product_specification`,
and `meeting_note`. Every indexed chunk
must carry `organization`, `document_id`, `title`, `document_type`, `department`,
`access_level`, `created_date`, and `source_path` metadata.

Access levels are:

- `internal`: all authenticated employees.
- `confidential`: Analyst or Administrator, limited to an allowed department unless Admin.
- `restricted`: Administrator only.

The server derives Pinecone filters from the trusted identity. An agent, tool call, prompt, or
retrieved document cannot weaken these filters.

## Supported tools

All tools are registered in one deterministic gateway, schema-validated, audited, and timed out.

| Tool ID | Purpose | Roles |
|---|---|---|
| `knowledge_search` | Hybrid search over authorized document chunks | all |
| `structured_analysis` | Allowlisted aggregations over supplied evidence records | Analyst, Administrator |
| `get_employee`, `search_employees` | Read synthetic employee directory entries | Analyst, Administrator |
| `get_service`, `search_services` | Read synthetic service ownership/catalogue data | Analyst, Administrator |
| `get_incident`, `search_incidents` | Read synthetic incident records | Analyst, Administrator |
| `modify_incident` | Approval-gated synthetic incident status update | Administrator |

`structured_analysis` is not a Python REPL. It exposes named operations such as count, group,
trend, and distribution over bounded structured input. There is no shell tool, dynamic tool
loading, filesystem tool, external URL fetcher, or arbitrary write-capable enterprise tool.
`modify_incident` is the single approval-gated synthetic write demonstration.

## Memory

- Session memory is mandatory and keyed by trusted `user_id` plus `conversation_id`.
- It stores messages, compact summaries, evidence references, and safe activity metadata.
- Conversation ownership is checked before every read or write.
- Explicit, user-owned long-term preferences are supported separately from conversation memory.
- Raw chain-of-thought, credentials, and full confidential documents are never stored.

## Provider and deployment assumptions

- OpenAI is the optional semantic-routing and synthesis provider; the local profile uses a
  deterministic router without cloud credentials.
- Pinecone is the dense vector store; BM25 operates over the POC corpus/index representation.
- PostgreSQL owns conversation checkpoints; Redis owns token-bucket state.
- LangSmith is optional for trace inspection, with sensitive content redacted.
- Local Docker Compose deployment is the initial target; production HA is excluded.

## Success boundary

The POC is successful when the ten golden scenarios pass, activity is observable without
revealing hidden reasoning, citations resolve to authorized evidence, and dependency failures
produce the documented safe or degraded result. Quality beyond that baseline is measured but
does not expand scope during implementation.
