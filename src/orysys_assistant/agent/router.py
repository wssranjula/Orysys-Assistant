"""Model-backed supervisor routing with a strict structured decision contract."""

from typing import Any, Protocol

from langsmith import traceable

from orysys_assistant.agent.models import AgentModel, AgentRoute


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
        result = await self._agent.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]}
        )
        return RouteDecision.model_validate(result["structured_response"])
