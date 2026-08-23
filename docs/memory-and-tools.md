# Conversation Memory and Enterprise Tools

Conversation memory, explicit preferences, and the remaining read-only tools keep security decisions
outside prompts.

## Conversation ownership and storage

Conversation access is keyed by `user_id + conversation_id`. The API resolves the authenticated
identity before creating or loading a conversation, and ownership mismatch returns `403` before an
SSE stream begins.

Stored fields are deliberately small:

- recent user and final assistant messages
- a bounded compact transcript summary
- deduplicated evidence IDs
- LangGraph checkpoints under the same composite thread key

Authentication tokens, full retrieved documents, raw MCP payload history, and hidden reasoning are
not written to conversation memory. PostgreSQL is the Compose/deployment backend. The in-memory
repository and checkpointer are deterministic local-test seams. Checkpoint serialization disables
pickle fallback and explicitly permits only the application state model classes required by the
research graph.

Explicit long-term preferences are stored separately from conversation messages. A caller can list,
write, or delete only their own preferences through `/v1/memory/preferences`; consent is explicit
on writes. Preferences are supplied as a distinct bounded context block, not merged into raw chat
history.

## Controlled structured analysis

`structured_analysis` accepts a strict record array and one of five enum operations: `count_by`,
`group_by`, `trend_by_date`, `top_values`, or `percentage`. Record count, fields, output rows, gateway
deadline, and response size are bounded. There is no source-code, expression, module, filesystem, or
shell input. Analyst and Administrator roles have the required capability; Viewer does not.

## Read-only MCP tools

The stateless mock MCP server exposes:

- `get_employee` and `search_employees`
- `get_service` and `search_services`
- `get_incident` and `search_incidents`

The HTTP adapter opens a streamable-HTTP MCP session, initializes it, invokes one named tool, and
returns its structured result. Local tests use the same contracts with an in-memory adapter. All six
registrations cross `ToolGateway` for allowlisting, RBAC, reserved-context rejection, Pydantic input
validation, timeout, maximum result bytes, structured errors, audit logging, and LangSmith tracing.
Timeouts become a degraded enterprise result and a visible activity event rather than crashing the
root agent.

## Activity events

Chat streams now include memory load/update events and tool started/completed/denied or degraded
events. Streamlit preserves the conversation ID across reruns and can reload persisted messages from
the conversation endpoint when its local message list is empty.
