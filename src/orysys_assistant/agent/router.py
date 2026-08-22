"""Model-backed supervisor routing with a strict structured decision contract."""

from typing import Any, Protocol

from langsmith import traceable

from orysys_assistant.agent.models import AgentModel, AgentRoute

_DOCUMENT_SOURCE_FAMILIES = (
    ("incident", "incidents", "postmortem", "postmortems"),
    ("meeting note", "meeting notes", "minutes"),
    ("runbook", "runbooks"),
    ("architecture", "architecture document", "architecture documents"),
    ("policy", "policies"),
    ("specification", "specifications"),
)


class RouteDecision(AgentModel):
    route: AgentRoute


class AgentRouter(Protocol):
    async def route(self, question: str, conversation_context: str = "") -> RouteDecision: ...


class LLMIntentRouter:
    """Ask a supervisor agent to select one code-controlled graph branch."""

    def __init__(self, agent: Any) -> None:
        self._agent = agent

    @traceable(
        name="llm-supervisor-routing",
        run_type="chain",
        metadata={"agent": "supervisor", "operation": "routing"},
    )
    async def route(self, question: str, conversation_context: str = "") -> RouteDecision:
        prompt = (
            f"Current user request:\n{question}\n\n"
            "Relevant prior conversation context:\n"
            f"{conversation_context or 'No prior context.'}"
        )
        result = await self._agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
        decision = RouteDecision.model_validate(result["structured_response"])
        if decision.route is AgentRoute.ENTERPRISE and self._is_multi_source_document_request(
            question
        ):
            return RouteDecision(route=AgentRoute.RESEARCH)
        return decision

    @staticmethod
    def _is_multi_source_document_request(question: str) -> bool:
        """Prevent a single-record enterprise lookup from replacing document research."""
        normalized = " ".join(question.lower().split())
        source_families = sum(
            any(marker in normalized for marker in family) for family in _DOCUMENT_SOURCE_FAMILIES
        )
        return source_families >= 2


class DeterministicIntentRouter:
    """Credential-free router for the local deterministic deployment profile.

    This intentionally conservative classifier keeps local Compose usable without a
    model credential.  Hosted deployments use ``LLMIntentRouter`` instead.
    """

    async def route(self, question: str, conversation_context: str = "") -> RouteDecision:
        del conversation_context
        normalized = " ".join(question.lower().split())
        if self._is_out_of_scope(normalized):
            return RouteDecision(route=AgentRoute.OUT_OF_SCOPE)
        if LLMIntentRouter._is_multi_source_document_request(question) or any(
            marker in normalized
            for marker in ("investigate", "compare", "recurring", "root cause", "across ")
        ):
            return RouteDecision(route=AgentRoute.RESEARCH)
        if any(
            marker in normalized
            for marker in ("count", "percentage", "percent", "distribution", "trend", "how many")
        ):
            return RouteDecision(route=AgentRoute.ANALYSIS)
        if any(
            marker in normalized
            for marker in ("who owns", "on-call", "employee", "service catalog", "incident system")
        ):
            return RouteDecision(route=AgentRoute.ENTERPRISE)
        return RouteDecision(route=AgentRoute.DIRECT_KNOWLEDGE)

    @staticmethod
    def _is_out_of_scope(question: str) -> bool:
        return question in {"hi", "hello", "hey"} or any(
            marker in question
            for marker in ("joke", "poem", "recipe", "movie", "entertainment", "personal advice")
        )
