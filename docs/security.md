# Security Boundary (Phase 2)

## Identity and credentials

The POC has exactly four fictional users. Application user records contain only unique salts
and PBKDF2-HMAC-SHA256 digests with 310,000 iterations; the public demo credentials are
documented separately for evaluators. Authentication uses a generic
failure message and performs hash work for unknown usernames to reduce enumeration timing
differences. A successful login returns a configurable opaque token; it is not a self-asserted
JWT. Each protected request maps that token back to a server-owned immutable identity.

This is intentionally not production identity management. Federation, token rotation,
revocation, MFA, login throttling, and secret-vault storage are deployment follow-ups.

## Trusted request context

The backend derives this chain and never accepts it from request JSON:

```text
bearer token
  -> UserIdentity(user_id, role, department)
  -> AccessScope(organization, namespace, access levels, departments)
  -> fixed retrieval metadata filter
```

Viewer scope permits internal documents in the user's department plus all-employee documents.
Analyst scope additionally permits confidential documents but retains the department boundary.
Administrator scope permits internal, confidential, and restricted documents without a
department filter. The Phase 3 retrieval adapter must accept an `AccessScope`; agents cannot
construct or modify its filters.

## Central authorization policy

There is one role/capability matrix in `security/authorization.py`. Prompts, UI code, MCP code,
and individual tools do not duplicate policy. Every decision emits a structured audit record
and the policy boundary is LangSmith-traceable when tracing is enabled.

| Capability | Viewer | Analyst | Administrator |
|---|---:|---:|---:|
| chat | yes | yes | yes |
| knowledge search | yes | yes | yes |
| structured analysis | no | yes | yes |
| MCP read | no | yes | yes |
| administrative tools | no | no | yes |
| restricted documents | no | no | yes |

## Tool gateway

Every tool uses one gateway. Processing order is allowlist lookup, role/capability check,
reserved-context rejection, Pydantic parameter validation, timeout, execution, result-size
check, and audit. The keys `role`, `user_id`, `access_level`, `namespace`, `organization_id`, and
`conversation_owner` are rejected anywhere in client/model-supplied parameter structures.
Denied requests cannot invoke a handler.

## Rate limiting

The deployed backend uses a Redis Lua script to atomically refill and consume per-user buckets
using Redis server time. This shares state across concurrent API instances and avoids
read/modify/write races. Bucket capacity and refill rate are configurable per role. Exhaustion
returns HTTP 429 with `Retry-After` and the common safe error envelope before any model call.

An asyncio-lock-protected memory adapter exists only for deterministic tests and explicitly
selected single-process development. It is not an automatic fallback: if Redis is unavailable,
readiness fails and chat returns a safe 503 rather than silently weakening the shared limit.

## Audit and redaction

Structured events include request ID, trusted user ID, role, capability/tool, result, and safe
error type. They exclude passwords, bearer tokens, authorization headers, prompt bodies, full
document text, MCP payloads, and hidden reasoning. Stream activity reports that authentication
and limiting completed but does not expose tokens, filters, or policy internals.
