# Frozen POC Scope and Assumptions

Status: accepted for Phase 0. Changes require a new ADR or an amendment to an existing ADR.

## Business boundary

- The assistant represents the fictional **Commercial Bank**.
- There is one organization and one Pinecone namespace: `commercial-bank`.
- The corpus is synthetic and contains no real customer, employee, or bank data.
- The assistant is informational. It does not transact, approve, modify records, or provide
  account-specific or unsupported financial advice.
- English is the only supported language in the POC.

## Users and roles

Authentication uses opaque bearer tokens mapped server-side to these fixtures. The client may
never submit or override `user_id`, `role`, `namespace`, departments, or access levels.

| User ID | Display name | Role | Department |
|---|---|---|---|
| `user-viewer-01` | Vina Perera | Viewer | Retail Banking |
| `user-analyst-01` | Arun Silva | Analyst | Payments |
| `user-admin-01` | Maya Fernando | Administrator | Technology |

| Capability | Viewer | Analyst | Administrator |
|---|---:|---:|---:|
| Chat and knowledge search | Yes | Yes | Yes |
| Structured analysis | No | Yes | Yes |
| Employee/service/incident MCP reads | No | Yes | Yes |
| System diagnostic read | No | No | Yes |
| Change data or policy | No | No | No |

## Documents and access

Supported types are `policy`, `architecture`, `runbook`, and `incident`. Every indexed chunk
must carry `organization`, `document_id`, `title`, `document_type`, `department`,
`access_level`, `created_date`, and `source_path` metadata.

Access levels are:

- `internal`: all authenticated employees.
- `confidential`: Analyst or Administrator, limited to an allowed department unless Admin.
- `restricted`: Administrator only.

The server derives Pinecone filters from the trusted identity. An agent, tool call, prompt, or
retrieved document cannot weaken these filters.

## Supported tools

All tools are registered in one deterministic gateway, schema-validated, audited, timed out,
and read-only.

| Tool ID | Purpose | Roles |
|---|---|---|
| `knowledge_search` | Hybrid search over authorized document chunks | all |
| `structured_analysis` | Allowlisted aggregations over supplied evidence records | Analyst, Administrator |
| `employee_directory.lookup` | Lookup synthetic employee directory entries | Analyst, Administrator |
| `service_catalog.search` | Search synthetic service ownership/catalogue data | Analyst, Administrator |
| `incident_records.search` | Search synthetic structured incident records | Analyst, Administrator |
| `system_diagnostics.read` | Read synthetic platform diagnostic status | Administrator |

`structured_analysis` is not a Python REPL. It exposes named operations such as count, group,
trend, and distribution over bounded structured input. There is no shell tool, dynamic tool
loading, filesystem tool, external URL fetcher, or write-capable enterprise tool.

## Memory

- Session memory is mandatory and keyed by trusted `user_id` plus `conversation_id`.
- It stores messages, compact summaries, evidence references, and safe activity metadata.
- Conversation ownership is checked before every read or write.
- Cross-session personalization and long-term memory are optional and excluded initially.
- Raw chain-of-thought, credentials, and full confidential documents are never stored.

## Provider and deployment assumptions

- OpenAI is the initial LLM and embedding provider; provider access is behind adapters.
- Pinecone is the dense vector store; BM25 operates over the POC corpus/index representation.
- PostgreSQL owns conversation checkpoints; Redis owns token-bucket state.
- LangSmith is mandatory for trace inspection, with sensitive content redacted.
- Local Docker Compose deployment is the initial target; production HA is excluded.

## Success boundary

The POC is successful when the ten golden scenarios pass, activity is observable without
revealing hidden reasoning, citations resolve to authorized evidence, and dependency failures
produce the documented safe or degraded result. Quality beyond that baseline is measured but
does not expand scope during implementation.

