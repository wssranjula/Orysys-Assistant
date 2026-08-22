"""Streamlit chat and real-time activity interface for the walking skeleton."""

import json
import os
from collections.abc import Iterator
from typing import Any

import httpx
import streamlit as st

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
    lines = []
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
        lines.append(f"{marker} **{label}**{suffix}  \n{event.get('message', '')}")
    placeholder.markdown("\n\n".join(lines))


def stream_turn(message: str, answer_placeholder: Any, activity_placeholder: Any) -> str:
    payload: dict[str, Any] = {"message": message}
    if st.session_state.conversation_id:
        payload["conversation_id"] = st.session_state.conversation_id

    answer = ""
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
            body = response.read().json()
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
                answer = event["answer"]
                st.session_state.conversation_id = event["conversation_id"]
                if event.get("warnings"):
                    warning = " · ".join(event["warnings"])
                    answer_placeholder.markdown(f"{answer}\n\n> ⚠️ {warning}")
                else:
                    answer_placeholder.markdown(answer)
    return answer


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


st.set_page_config(page_title="Commercial Bank AI Assistant", page_icon="🏦", layout="wide")
st.title("Commercial Bank AI Assistant")
st.caption("Phase 1 walking skeleton · streamed mock response with inspectable activity")

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

    prompt = st.chat_input("Ask a Commercial Bank knowledge question")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            answer_placeholder = st.empty()
            try:
                answer = stream_turn(prompt, answer_placeholder, activity_placeholder)
            except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as exc:
                answer = f"The assistant is unavailable: {exc}"
                answer_placeholder.error(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})
