"""Streamlit chat and real-time activity interface for the walking skeleton."""

import json
import os
from collections.abc import Iterator
from typing import Any

import httpx
import streamlit as st

from orysys_assistant.observability.activity import project_activity_panel

API_BASE_URL = os.getenv("UI_API_BASE_URL", "http://localhost:8000").rstrip("/")


def iter_sse(response: httpx.Response) -> Iterator[tuple[str, dict[str, Any]]]:
    """Parse named SSE events from an HTTPX streaming response."""

    event_name = "message"
    data_lines: list[str] = []
    for line in response.iter_lines():
        if not line:
            if data_lines:
                yield event_name, json.loads("\n".join(data_lines))
            event_name = "message"
            data_lines = []
        elif line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").lstrip())

    if data_lines:
        yield event_name, json.loads("\n".join(data_lines))


def _status_badge(label: str, value: str) -> str:
    tone = {
        "completed": "#1f7a4d",
        "in_progress": "#2563eb",
        "started": "#2563eb",
        "pending": "#64748b",
        "failed": "#dc2626",
        "denied": "#dc2626",
        "degraded": "#d97706",
    }.get(value.lower(), "#64748b")
    return (
        f'<span style="display:inline-block;padding:0.15rem 0.55rem;border-radius:999px;'
        f'font-size:0.75rem;font-weight:600;background:{tone}22;color:{tone};'
        f'border:1px solid {tone}44;">{label}: {value.replace("_", " ").title()}</span>'
    )


def render_activity_timeline(
    events: list[dict[str, Any]],
    placeholder: Any | None = None,
    *,
    expanded: bool = False,
    processing: bool = False,
) -> None:
    target = placeholder or st
    with target.container():
        with st.expander("Agent timeline", expanded=expanded or processing):
            if not events:
                st.caption("Waiting for agent events…")
                return
            for event in events[-12:]:
                status = event.get("status", "in_progress")
                marker = {
                    "completed": "✅",
                    "failed": "❌",
                    "denied": "⛔",
                    "degraded": "⚠️",
                    "started": "▶️",
                }.get(status, "⏳")
                label = event.get("event_type", "activity").replace("_", " ").title()
                node = event.get("node")
                suffix = f" · `{node}`" if node else ""
                st.markdown(f"{marker} **{label}**{suffix}  \n{event.get('message', '')}")


def render_activity_summary(
    events: list[dict[str, Any]],
    placeholder: Any,
    *,
    processing: bool = False,
) -> None:
    if not events:
        with placeholder.container():
            if processing:
                st.info("Processing your question — live agent status will appear here.")
            else:
                st.info("Agent status will appear here when you ask a question.")
        return

    panel = project_activity_panel(events)
    with placeholder.container():
        header_left, header_right = st.columns([3, 1])
        with header_left:
            st.markdown("##### Live agent run")
        with header_right:
            if processing:
                st.markdown(
                    '<p style="text-align:right;margin:0.2rem 0 0;font-size:0.8rem;'
                    'color:#2563eb;font-weight:600;">● Streaming</p>',
                    unsafe_allow_html=True,
                )

        if panel.degraded:
            st.warning("This request is operating in partial or degraded mode.")

        with st.container(border=True):
            agent_column, node_column = st.columns(2)
            agent_column.caption("Current agent")
            agent_column.markdown(f"**{panel.current_agent.replace('_', ' ').title()}**")
            node_column.caption("Graph node")
            node_column.markdown(f"**`{panel.current_node}`**")
            st.markdown(panel.plan_summary)

        if panel.research_todos:
            with st.container(border=True):
                st.markdown("**Research tasks**")
                for todo in panel.research_todos:
                    marker = {
                        "completed": "✅",
                        "in_progress": "🔄",
                        "pending": "⬜",
                    }.get(todo.get("status", "pending"), "⬜")
                    st.markdown(f"{marker} {todo.get('content', '')}")

        with st.container(border=True):
            tool_column, retrieval_column = st.columns(2)
            tool_column.caption("Active tool")
            tool_column.markdown(f"**`{panel.tool_name}`**")
            retrieval_column.caption("Retrieval mode")
            retrieval_column.markdown(f"**{panel.retrieval_mode}**")
            candidate_column, evidence_column = st.columns(2)
            candidate_column.metric("Candidates", panel.candidate_count)
            evidence_column.metric("Selected evidence", panel.selected_evidence_count)
            if panel.retrieval_filters:
                filters = " · ".join(
                    f"{key.replace('_', ' ')}: `{value}`"
                    for key, value in panel.retrieval_filters.items()
                )
                st.caption(f"Filters · {filters}")

        st.markdown(
            f"{_status_badge('Memory', panel.memory_status)} "
            f"{_status_badge('Validation', panel.validation_status)}",
            unsafe_allow_html=True,
        )

        details = [f"Request `{panel.request_id[:8]}…`" if panel.request_id else ""]
        if panel.langsmith_run_id:
            details.append(f"LangSmith `{panel.langsmith_run_id[:8]}…`")
        st.caption(" · ".join(item for item in details if item))


def render_generating_indicator(placeholder: Any) -> None:
    placeholder.markdown(
        """
        <p class="generating-indicator">
          <span class="generating-dot"></span>
          Generating response…
        </p>
        """,
        unsafe_allow_html=True,
    )


def stream_turn(
    message: str,
    answer_placeholder: Any,
    summary_placeholder: Any,
    timeline_placeholder: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"message": message}
    if st.session_state.conversation_id:
        payload["conversation_id"] = st.session_state.conversation_id

    answer = ""
    answer_is_provisional = False
    final_response: dict[str, Any] = {}
    render_generating_indicator(answer_placeholder)
    render_activity_timeline([], timeline_placeholder, expanded=True, processing=True)
    with (
        httpx.Client(timeout=httpx.Timeout(130, connect=5)) as client,
        client.stream(
            "POST",
            f"{API_BASE_URL}/v1/chat/stream",
            json=payload,
            headers={"Authorization": f"Bearer {st.session_state.access_token}"},
        ) as response,
    ):
        if response.is_error:
            body = json.loads(response.read())
            raise RuntimeError(body.get("error", {}).get("message", "API request failed"))

        for event_name, event in iter_sse(response):
            if event_name == "activity":
                st.session_state.activities.append(event)
                render_activity_summary(
                    st.session_state.activities,
                    summary_placeholder,
                    processing=True,
                )
                render_activity_timeline(
                    st.session_state.activities,
                    timeline_placeholder,
                    expanded=True,
                    processing=True,
                )
            elif event_name == "answer_delta":
                answer += event["text"]
                answer_is_provisional = bool(event.get("provisional", False))
                st.session_state.conversation_id = event["conversation_id"]
                draft_note = (
                    "\n\n> Draft answer — validating before the final response."
                    if answer_is_provisional
                    else ""
                )
                answer_placeholder.markdown(f"{answer}{draft_note} ▌")
            elif event_name == "final":
                final_response = event
                answer = event["answer"]
                answer_is_provisional = False
                st.session_state.conversation_id = event["conversation_id"]
                if event.get("warnings"):
                    warning = " · ".join(event["warnings"])
                    answer_placeholder.markdown(f"{answer}\n\n> ⚠️ {warning}")
                else:
                    answer_placeholder.markdown(answer)
    return final_response or {"answer": answer, "citations": [], "warnings": []}


def render_citations(citations: list[dict[str, Any]]) -> None:
    if not citations:
        return
    with st.expander(f"Evidence sources ({len(citations)})"):
        for citation in citations:
            st.markdown(
                f"**[{citation['citation_id']}] {citation['title']}**  \n"
                f"Document: `{citation['document_id']}` · Chunk: `{citation['chunk_id']}`  \n"
                f"Source: `{citation['source_path']}`"
            )


def login(username: str, password: str) -> dict[str, Any]:
    with httpx.Client(timeout=httpx.Timeout(15, connect=5)) as client:
        response = client.post(
            f"{API_BASE_URL}/v1/auth/token",
            json={"username": username, "password": password},
        )
    if response.is_error:
        body = response.json()
        raise RuntimeError(body.get("error", {}).get("message", "Authentication failed"))
    return response.json()


def load_conversation(conversation_id: str) -> list[dict[str, str]]:
    with httpx.Client(timeout=httpx.Timeout(15, connect=5)) as client:
        response = client.get(
            f"{API_BASE_URL}/v1/conversations/{conversation_id}",
            headers={"Authorization": f"Bearer {st.session_state.access_token}"},
        )
    if response.is_error:
        raise RuntimeError("Conversation memory could not be loaded")
    return response.json().get("messages", [])


def _authorized_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {st.session_state.access_token}"}


def _api_error(response: httpx.Response, fallback: str) -> RuntimeError:
    try:
        message = response.json().get("error", {}).get("message", fallback)
    except json.JSONDecodeError:
        message = fallback
    return RuntimeError(message)


def list_approvals() -> list[dict[str, Any]]:
    with httpx.Client(timeout=httpx.Timeout(15, connect=5)) as client:
        response = client.get(
            f"{API_BASE_URL}/v1/approvals",
            headers=_authorized_headers(),
        )
    if response.is_error:
        raise _api_error(response, "Approvals could not be loaded")
    return response.json().get("approvals", [])


def create_approval(
    incident_id: str,
    incident_status: str,
    change_reason: str,
    approval_reason: str,
) -> dict[str, Any]:
    payload = {
        "action": "modify_incident",
        "parameters": {
            "incident_id": incident_id.strip().upper(),
            "status": incident_status,
            "reason": change_reason.strip(),
        },
        "reason": approval_reason.strip(),
    }
    with httpx.Client(timeout=httpx.Timeout(15, connect=5)) as client:
        response = client.post(
            f"{API_BASE_URL}/v1/approvals",
            json=payload,
            headers=_authorized_headers(),
        )
    if response.is_error:
        raise _api_error(response, "Approval request could not be created")
    return response.json()


def decide_approval(approval_id: str, approved: bool) -> dict[str, Any]:
    with httpx.Client(timeout=httpx.Timeout(15, connect=5)) as client:
        response = client.post(
            f"{API_BASE_URL}/v1/approvals/{approval_id}/decision",
            json={"approved": approved},
            headers=_authorized_headers(),
        )
    if response.is_error:
        raise _api_error(response, "Approval decision could not be saved")
    return response.json()


def render_approval_center() -> None:
    st.markdown("#### Approval center")
    st.caption("Review high-impact changes before any administrative write is executed.")

    with st.expander("Request an incident change", expanded=False):
        with st.form("incident_approval_form", clear_on_submit=True):
            incident_id = st.text_input("Incident ID", placeholder="INC-2026-004")
            incident_status = st.selectbox(
                "Proposed status", ("investigating", "monitoring", "resolved")
            )
            change_reason = st.text_area(
                "Operational justification",
                placeholder="Describe the checks or evidence supporting this status change.",
            )
            approval_reason = st.text_input(
                "Approval summary", placeholder="Why this action needs human approval"
            )
            submitted = st.form_submit_button(
                "Submit for approval", type="primary", use_container_width=True
            )
        if submitted:
            try:
                created = create_approval(
                    incident_id, incident_status, change_reason, approval_reason
                )
            except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as exc:
                st.error(str(exc))
            else:
                st.success(f"Approval `{created['approval_id']}` is awaiting a decision.")

    try:
        approvals = list_approvals()
    except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as exc:
        st.error(str(exc))
        return

    pending = [item for item in approvals if item["status"] == "pending"]
    executed = sum(item["status"] == "executed" for item in approvals)
    rejected = sum(item["status"] == "rejected" for item in approvals)
    pending_metric, executed_metric, rejected_metric = st.columns(3)
    pending_metric.metric("Pending", len(pending))
    executed_metric.metric("Executed", executed)
    rejected_metric.metric("Rejected", rejected)

    if not pending:
        st.info("No approval requests are waiting for review.")
    for item in pending:
        parameters = item["parameters"]
        with st.container(border=True):
            st.markdown(f"**{parameters['incident_id']} → {parameters['status'].title()}**")
            st.caption(f"Request `{item['approval_id']}` · by `{item['requester_id']}`")
            st.write(parameters["reason"])
            st.caption(f"Approval summary: {item['reason']}")
            approve_column, reject_column = st.columns(2)
            if approve_column.button(
                "Approve",
                key=f"approve-{item['approval_id']}",
                type="primary",
                use_container_width=True,
            ):
                try:
                    decide_approval(item["approval_id"], True)
                except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as exc:
                    st.error(str(exc))
                else:
                    st.toast("Action approved and executed.", icon="✅")
                    st.rerun()
            if reject_column.button(
                "Reject",
                key=f"reject-{item['approval_id']}",
                use_container_width=True,
            ):
                try:
                    decide_approval(item["approval_id"], False)
                except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as exc:
                    st.error(str(exc))
                else:
                    st.toast("Action rejected. No write was performed.", icon="🛑")
                    st.rerun()


st.set_page_config(page_title="Commercial Bank AI Assistant", page_icon="🏦", layout="wide")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "activities" not in st.session_state:
    st.session_state.activities = []
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "access_token" not in st.session_state:
    st.session_state.access_token = None
if "identity" not in st.session_state:
    st.session_state.identity = None

st.markdown(
    """
    <style>
      .stApp { background: var(--background-color); }
      [data-testid="stHeader"] { background: transparent; }
      [data-testid="stSidebar"] {
        border-right: 1px solid rgba(128, 128, 128, 0.22);
        background: var(--secondary-background-color);
      }
      [data-testid="stMetric"] {
        background: var(--background-color);
        border: 1px solid rgba(128, 128, 128, 0.18);
        border-radius: 10px;
        padding: 0.55rem 0.7rem;
      }
      .stChatMessage {
        background: var(--secondary-background-color);
        border: 1px solid rgba(128, 128, 128, 0.18);
        border-radius: 14px;
        padding: 0.35rem 0.15rem;
      }
      div[data-testid="stVerticalBlockBorderWrapper"] {
        background: var(--secondary-background-color);
        border-radius: 12px;
        border-color: rgba(128, 128, 128, 0.18) !important;
      }
      [data-testid="stTabs"] button {
        font-weight: 600;
      }
      [data-testid="stChatInput"] textarea {
        border-radius: 12px !important;
      }
      .block-container {
        padding-top: 1.5rem;
        max-width: 1180px;
      }
      .generating-indicator {
        color: #64748b;
        font-size: 0.95rem;
        margin: 0.2rem 0 0.5rem;
        display: flex;
        align-items: center;
        gap: 0.55rem;
      }
      .generating-dot {
        width: 0.55rem;
        height: 0.55rem;
        border-radius: 999px;
        background: #2563eb;
        display: inline-block;
        animation: generating-pulse 1.1s ease-in-out infinite;
      }
      @keyframes generating-pulse {
        0%, 100% { opacity: 0.35; transform: scale(0.85); }
        50% { opacity: 1; transform: scale(1); }
      }
    </style>
    """,
    unsafe_allow_html=True,
)
header_left, header_right = st.columns([4, 1])
with header_left:
    st.title("Commercial Bank AI Assistant")
    st.caption("Grounded enterprise intelligence · human-controlled actions · cited evidence")
with header_right:
    turn_count = sum(1 for message in st.session_state.messages if message["role"] == "user")
    st.metric("Turns", turn_count)

if (
    st.session_state.access_token
    and st.session_state.conversation_id
    and not st.session_state.messages
):
    try:
        st.session_state.messages = load_conversation(st.session_state.conversation_id)
    except (httpx.HTTPError, RuntimeError, json.JSONDecodeError):
        st.session_state.messages = []

if not st.session_state.access_token:
    st.subheader("Sign in")
    st.caption("Use one of the Phase 2 fictional Commercial Bank accounts.")
    with st.form("login_form"):
        username = st.text_input("Username", value="viewer@commercialbank.test")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", use_container_width=True)
    if submitted:
        try:
            identity = login(username, password)
        except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as exc:
            st.error(str(exc))
        else:
            st.session_state.access_token = identity.pop("access_token")
            st.session_state.identity = identity
            st.rerun()
    st.stop()

with st.sidebar:
    identity = st.session_state.identity
    st.markdown("### Session")
    st.write(f"**{identity['display_name']}**")
    st.caption(f"Role · {identity['role'].title()}")
    conversation_label = (
        f"`{st.session_state.conversation_id[:8]}…`"
        if st.session_state.conversation_id
        else "New conversation"
    )
    st.caption(f"Conversation · {conversation_label}")
    st.divider()
    if st.button("Clear session", use_container_width=True):
        st.session_state.messages = []
        st.session_state.activities = []
        st.session_state.conversation_id = None
        st.rerun()
    if st.button("Sign out", use_container_width=True):
        st.session_state.clear()
        st.rerun()

prompt = st.chat_input("Ask a Commercial Bank knowledge question")

if prompt:
    st.session_state.activities = []

chat_column, activity_column = st.columns([2, 1], gap="large")

with activity_column:
    is_administrator = st.session_state.identity["role"] == "administrator"
    tabs = st.tabs(["Agent activity", "Approvals"] if is_administrator else ["Agent activity"])
    with tabs[0]:
        summary_placeholder = st.empty()
        render_activity_summary(
            st.session_state.activities,
            summary_placeholder,
            processing=bool(prompt),
        )
    if is_administrator:
        with tabs[1]:
            render_approval_center()

with chat_column:
    if not st.session_state.messages:
        st.info(
            "Ask about policies, architecture, incidents, or runbooks. "
            "Each answer includes cited evidence and an agent timeline below the response."
        )

    for index, chat_message in enumerate(st.session_state.messages):
        with st.chat_message(chat_message["role"]):
            st.markdown(chat_message["content"])
            render_citations(chat_message.get("citations", []))
            if chat_message["role"] == "assistant" and chat_message.get("activities"):
                render_activity_timeline(
                    chat_message["activities"],
                    expanded=index == len(st.session_state.messages) - 1,
                )

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            answer_placeholder = st.empty()
            citations_placeholder = st.empty()
            timeline_placeholder = st.empty()
            try:
                with st.spinner("Generating response…"):
                    final_response = stream_turn(
                        prompt,
                        answer_placeholder,
                        summary_placeholder,
                        timeline_placeholder,
                    )
                answer = final_response["answer"]
                if not answer.strip():
                    answer_placeholder.warning("The assistant returned an empty response.")
                with citations_placeholder.container():
                    render_citations(final_response.get("citations", []))
                render_activity_summary(st.session_state.activities, summary_placeholder)
                render_activity_timeline(st.session_state.activities, timeline_placeholder)
            except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as exc:
                answer = f"The assistant is unavailable: {exc}"
                final_response = {"citations": []}
                answer_placeholder.error(answer)
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": answer,
                    "citations": final_response.get("citations", []),
                    "activities": list(st.session_state.activities),
                }
            )
