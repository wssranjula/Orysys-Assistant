"""Auditable deterministic intent routing used before model autonomy."""

import re

from langsmith import traceable

from orysys_assistant.agent.models import AgentRoute


class IntentRouter:
    _enterprise_patterns = (
        r"\b(owner|ownership|employee directory|service catalogue|service catalog)\b",
        r"\b(who owns|on-call|contact for)\b",
        r"\b(?:emp-[0-9]{3}|svc-[a-z]+-[0-9]{3}|inc-[0-9]{4}-[0-9]{3})\b",
    )
    _analysis_patterns = (
        r"\b(count|group|distribution|percentage|frequency|trend)\b",
        r"\b(by root cause|how many|breakdown)\b",
    )
    _research_patterns = (
        r"\b(all|across|recurring|compare|investigate|last year|multiple)\b",
        r"\b(summarize.+incidents|outages.+root causes|multi-document)\b",
    )

    @traceable(
        name="root-intent-routing",
        run_type="chain",
        metadata={"agent": "root_deep_agent", "operation": "routing"},
    )
    def route(self, question: str) -> AgentRoute:
        normalized = " ".join(question.lower().split())
        if self._matches(normalized, self._enterprise_patterns):
            return AgentRoute.ENTERPRISE
        if self._matches(normalized, self._analysis_patterns):
            return AgentRoute.ANALYSIS
        if self._matches(normalized, self._research_patterns):
            return AgentRoute.RESEARCH
        return AgentRoute.DIRECT_KNOWLEDGE

    @staticmethod
    def _matches(value: str, patterns: tuple[str, ...]) -> bool:
        return any(re.search(pattern, value) for pattern in patterns)
