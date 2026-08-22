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


st.set_page_config(page_title="Commercial Bank AI Assistant", page_icon="🏦", layout="wide")
st.title("Commercial Bank AI Assistant")
st.caption("Phase 8 · grounded assistant with safe real-time activity and cited evidence")

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
    st.subheader("Agent activity")
    activity_placeholder = st.empty()
    render_activity(st.session_state.activities, activity_placeholder)
    if st.button("Clear session", use_container_width=True):
        st.session_state.messages = []
        st.session_state.activities = []
        st.session_state.conversation_id = None
        st.rerun()

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
