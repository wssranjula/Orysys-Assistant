"""Safe activity metadata and evaluator-facing panel projections."""

from dataclasses import dataclass, field
from typing import Any

SAFE_ACTIVITY_METADATA_KEYS = frozenset(
    {
        "candidate_count",
        "degraded",
        "duration_ms",
        "error_code",
        "error_type",
        "evidence_count",
        "finding_count",
        "message_count",
        "partial",
        "plan_summary",
        "remaining",
        "repair_attempted",
        "repaired",
        "retrieval_filters",
        "retrieval_mode",
        "route",
        "rows_processed",
        "selected_evidence_count",
        "status",
        "sufficient",
        "task_count",
        "todo_content",
        "todo_id",
        "todo_status",
        "todos",
        "tool_name",
    }
)
SAFE_RETRIEVAL_FILTER_KEYS = frozenset(
    {"department", "document_type", "created_after", "created_before"}
)


def sanitize_activity_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Drop unknown or display-unsafe metadata before it reaches an SSE client."""
    sanitized: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if key not in SAFE_ACTIVITY_METADATA_KEYS:
            continue
        if key == "todos":
            if not isinstance(value, list):
                continue
            sanitized[key] = [
                {
                    "id": str(item.get("id", ""))[:80],
                    "content": str(item.get("content", ""))[:300],
                    "status": str(item.get("status", "pending"))
                    if item.get("status") in {"pending", "in_progress", "completed"}
                    else "pending",
                }
                for item in value[:6]
                if isinstance(item, dict) and item.get("content")
            ]
        elif key == "retrieval_filters":
            if not isinstance(value, dict):
                continue
            sanitized[key] = {
                filter_key: str(filter_value)[:100]
                for filter_key, filter_value in value.items()
                if filter_key in SAFE_RETRIEVAL_FILTER_KEYS and filter_value is not None
            }
        elif isinstance(value, bool | int | float):
            sanitized[key] = value
        elif isinstance(value, str):
            sanitized[key] = value[:200]
    return sanitized


@dataclass(slots=True)
class ActivityPanelState:
    trace_id: str = ""
    current_agent: str = "Waiting"
    current_node: str = "—"
    plan_summary: str = "No plan yet"
    tool_name: str = "—"
    retrieval_mode: str = "—"
    retrieval_filters: dict[str, str] = field(default_factory=dict)
    candidate_count: int = 0
    selected_evidence_count: int = 0
    memory_status: str = "pending"
    validation_status: str = "pending"
    degraded: bool = False
    research_todos: list[dict[str, str]] = field(default_factory=list)


def project_activity_panel(events: list[dict[str, Any]]) -> ActivityPanelState:
    """Reduce sanitized activity events into the current UI summary."""
    state = ActivityPanelState()
    for event in events:
        metadata = sanitize_activity_metadata(event.get("metadata"))
        state.trace_id = str(event.get("request_id", state.trace_id))
        if event.get("agent"):
            state.current_agent = str(event["agent"])
        if event.get("node"):
            state.current_node = str(event["node"])
        if metadata.get("plan_summary"):
            state.plan_summary = str(metadata["plan_summary"])
        if isinstance(metadata.get("todos"), list):
            known_todos = {todo.get("id"): todo for todo in state.research_todos}
            for todo in metadata["todos"]:
                existing = known_todos.get(todo.get("id"))
                if existing is None:
                    state.research_todos.append(todo)
                else:
                    existing.update(todo)
        todo_id = metadata.get("todo_id")
        if isinstance(todo_id, str) and todo_id:
            updated = False
            for todo in state.research_todos:
                if todo.get("id") == todo_id:
                    todo["status"] = str(metadata.get("todo_status", todo["status"]))
                    updated = True
                    break
            if not updated and metadata.get("todo_content"):
                state.research_todos.append(
                    {
                        "id": todo_id,
                        "content": str(metadata["todo_content"]),
                        "status": str(metadata.get("todo_status", "pending")),
                    }
                )
        if metadata.get("tool_name"):
            state.tool_name = str(metadata["tool_name"])
        elif event.get("event_type") in {"tool_started", "tool_completed", "tool_denied"}:
            state.tool_name = str(event.get("node", "—"))
        if metadata.get("retrieval_mode"):
            state.retrieval_mode = str(metadata["retrieval_mode"])
        if isinstance(metadata.get("retrieval_filters"), dict):
            state.retrieval_filters = metadata["retrieval_filters"]
        if isinstance(metadata.get("candidate_count"), int):
            state.candidate_count = metadata["candidate_count"]
        selected = metadata.get("selected_evidence_count", metadata.get("evidence_count"))
        if isinstance(selected, int):
            state.selected_evidence_count = selected
        if event.get("event_type") in {"memory_loaded", "memory_updated"}:
            state.memory_status = str(event.get("status", "completed"))
        if event.get("event_type") in {
            "validation_started",
            "validation_completed",
            "validation_failed",
        }:
            state.validation_status = str(event.get("status", "in_progress"))
        if event.get("status") in {"degraded", "failed"} or metadata.get("partial"):
            state.degraded = True
    return state
