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


def render_activity(events: list[dict[str, Any]], placeholder: Any) -> None:
    if not events:
        placeholder.info("Agent activity will appear here.")
        return
    panel = project_activity_panel(events)
    with placeholder.container():
        if panel.degraded:
            st.warning("This request is operating in partial or degraded mode.")
        st.caption(f"Trace ID: `{panel.trace_id}`")
        agent_column, node_column = st.columns(2)
        agent_column.caption("Current agent")
        agent_column.markdown(f"**{panel.current_agent.replace('_', ' ').title()}**")
        node_column.caption("Graph node")
        node_column.markdown(f"**`{panel.current_node}`**")
        st.info(panel.plan_summary)
        if panel.research_todos:
            st.markdown("##### Research tasks")
            for todo in panel.research_todos:
                marker = {
                    "completed": "✅",
                    "in_progress": "🔄",
                    "pending": "⬜",
                }.get(todo.get("status", "pending"), "⬜")
                st.markdown(f"{marker} {todo.get('content', '')}")

        tool_column, retrieval_column = st.columns(2)
        tool_column.caption("Active tool")
        tool_column.markdown(f"**`{panel.tool_name}`**")
        retrieval_column.caption("Retrieval mode")
        retrieval_column.markdown(f"**{panel.retrieval_mode}**")
        candidate_column, evidence_column = st.columns(2)
        candidate_column.metric("Candidates", panel.candidate_count)
        evidence_column.metric("Selected evidence", panel.selected_evidence_count)
        st.caption(f"Memory: **{panel.memory_status}** · Validation: **{panel.validation_status}**")
        if panel.retrieval_filters:
            filters = " · ".join(
                f"{key.replace('_', ' ')}: `{value}`"
                for key, value in panel.retrieval_filters.items()
            )
            st.caption(f"Retrieval filters · {filters}")

        st.markdown("##### Timeline")
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


def stream_turn(message: str, answer_placeholder: Any, activity_placeholder: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"message": message}
    if st.session_state.conversation_id:
        payload["conversation_id"] = st.session_state.conversation_id

    answer = ""
    final_response: dict[str, Any] = {}
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
                render_activity(st.session_state.activities, activity_placeholder)
            elif event_name == "answer_delta":
                answer += event["text"]
                answer_placeholder.markdown(answer + "▌")
                st.session_state.conversation_id = event["conversation_id"]
            elif event_name == "final":
                final_response = event
                answer = event["answer"]
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
st.markdown(
    """
    <style>
      .stApp { background: var(--background-color); }
      [data-testid="stHeader"] { background: transparent; }
      [data-testid="stSidebar"] { border-right: 1px solid rgba(128, 128, 128, 0.22); }
      [data-testid="stMetric"] {
        background: var(--secondary-background-color);
        border: 1px solid rgba(128, 128, 128, 0.22);
        border-radius: 12px;
        padding: 0.7rem;
      }
      .stChatMessage {
        background: var(--secondary-background-color);
        border: 1px solid rgba(128, 128, 128, 0.22);
        border-radius: 14px;
      }
      div[data-testid="stVerticalBlockBorderWrapper"] {
        background: var(--secondary-background-color);
        border-radius: 14px;
      }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("Commercial Bank AI Assistant")
st.caption("Grounded enterprise intelligence · human-controlled actions · cited evidence")

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
    st.write(f"Signed in as **{identity['display_name']}**")
    st.caption(f"Role: {identity['role'].title()}")
    st.caption(f"Conversation: {st.session_state.conversation_id or 'New conversation'}")
    if st.button("Sign out", use_container_width=True):
        st.session_state.clear()
        st.rerun()

chat_column, activity_column = st.columns([2, 1], gap="large")

with activity_column:
    is_administrator = st.session_state.identity["role"] == "administrator"
    tabs = st.tabs(["Agent activity", "Approvals"] if is_administrator else ["Agent activity"])
    with tabs[0]:
        activity_placeholder = st.empty()
        render_activity(st.session_state.activities, activity_placeholder)
        if st.button("Clear session", use_container_width=True):
            st.session_state.messages = []
            st.session_state.activities = []
            st.session_state.conversation_id = None
            st.rerun()
    if is_administrator:
        with tabs[1]:
            render_approval_center()

with chat_column:
    for chat_message in st.session_state.messages:
        with st.chat_message(chat_message["role"]):
            st.markdown(chat_message["content"])
            render_citations(chat_message.get("citations", []))

    prompt = st.chat_input("Ask a Commercial Bank knowledge question")
    if prompt:
        st.session_state.activities = []
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            answer_placeholder = st.empty()
            try:
                final_response = stream_turn(prompt, answer_placeholder, activity_placeholder)
                answer = final_response["answer"]
                render_citations(final_response.get("citations", []))
            except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as exc:
                answer = f"The assistant is unavailable: {exc}"
                final_response = {"citations": []}
                answer_placeholder.error(answer)
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": answer,
                    "citations": final_response.get("citations", []),
                }
            )
