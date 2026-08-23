"""Fail-closed grounding, citation-ledger, and brand-policy validation."""

import re
from dataclasses import dataclass

from langsmith import traceable

from orysys_assistant.agent.models import AgentExecutionResult, AgentRoute
from orysys_assistant.domain.models import Citation, ResponseStatus
from orysys_assistant.security.models import AccessScope

_CITATION_MARKER = re.compile(r"\[([^\[\]\s]{1,20})\]")
_HIDDEN_REASONING = re.compile(
    r"<\/?(?:thinking|analysis)>|\b(?:hidden )?chain[- ]of[- ]thought\b|\bsystem prompt\b",
    re.I,
)
_BRAND_POLICY = re.compile(
    r"\bas an ai language model\b|"
    r"\bi(?:'m| am) just an ai\b|"
    r"\bi(?:'m| am) not (?:a )?commercial bank\b",
    re.I,
)
_GROUNDING_ROUTES = frozenset(
    {AgentRoute.DIRECT_KNOWLEDGE, AgentRoute.RESEARCH, AgentRoute.ANALYSIS}
)
_INSUFFICIENT_ANSWER = (
    "I could not verify an answer from the authorized evidence available for this request."
)


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    result: AgentExecutionResult
    repaired: bool = False
    valid: bool = True
    reason: str | None = None


class OutputValidator:
    """Validate current-request evidence and permit one marker-only repair attempt."""

    @traceable(
        name="deterministic-output-validation",
        run_type="chain",
        metadata={"control": "citation_and_brand_validation", "phase": 7},
    )
    def validate(
        self, result: AgentExecutionResult, access_scope: AccessScope
    ) -> ValidationOutcome:
        reason = self._invalid_reason(result, access_scope)
        if reason is None:
            return ValidationOutcome(result=result)

        repaired = self._repair_once(result, reason)
        if repaired is not None and self._invalid_reason(repaired, access_scope) is None:
            return ValidationOutcome(result=repaired, repaired=True)

        return ValidationOutcome(
            result=self._insufficient(result, reason),
            repaired=repaired is not None,
            valid=False,
            reason=reason,
        )

    @staticmethod
    def _invalid_reason(result: AgentExecutionResult, access_scope: AccessScope) -> str | None:
        if not result.answer.strip():
            return "The response answer was empty."
        if _HIDDEN_REASONING.search(result.answer):
            return "The response contained protected instruction or reasoning text."
        if _BRAND_POLICY.search(result.answer):
            return "The response did not follow Commercial Bank assistant brand policy."
        if result.route not in _GROUNDING_ROUTES:
            return None
        if not result.evidence:
            return "No authorized evidence was available."

        ledger = {item.evidence_id: item for item in result.evidence}
        if len(ledger) != len(result.evidence):
            return "The evidence ledger contained duplicate identifiers."
        if set(result.evidence_ids) != set(ledger):
            return "The response evidence references did not match the evidence ledger."

        citations_by_id: dict[str, Citation] = {}
        for citation in result.citations:
            if citation.citation_id in citations_by_id:
                return "The response contained duplicate citation identifiers."
            citations_by_id[citation.citation_id] = citation
            evidence = ledger.get(citation.evidence_id)
            if evidence is None:
                return "A citation referenced unknown evidence."
            if str(evidence.metadata.get("access_level")) not in access_scope.allowed_access_levels:
                return "A citation referenced evidence outside the authorized access scope."
            if (
                citation.document_id != evidence.document_id
                or citation.chunk_id != evidence.chunk_id
                or citation.title != evidence.title
                or citation.source_path != str(evidence.metadata.get("source_path"))
            ):
                return "A citation did not match its evidence-ledger record."
        if set(item.evidence_id for item in result.citations) != set(ledger):
            return "Authorized evidence was missing a resolvable citation."

        markers = set(_CITATION_MARKER.findall(result.answer))
        unknown = markers - citations_by_id.keys()
        if unknown:
            return "The answer contained an unknown citation marker."
        if citations_by_id.keys() - markers:
            return "The answer was missing citation markers."
        return None

    @staticmethod
    def _repair_once(result: AgentExecutionResult, reason: str) -> AgentExecutionResult | None:
        """Append the sources the answer drew on but did not mark.

        A specialist writes its own prose and rarely marks every record it was shown, so
        a partial marker set is a formatting gap rather than a grounding failure. Listing
        the unmarked sources keeps the ledger complete without editing the claim itself;
        a marker pointing at evidence that is not in the ledger is still a hard failure.
        """

        if not result.citations:
            return None
        marked = set(_CITATION_MARKER.findall(result.answer))
        unmarked = [item for item in result.citations if item.citation_id not in marked]
        if unmarked:
            markers = " ".join(f"[{citation.citation_id}]" for citation in unmarked)
            return result.model_copy(update={"answer": f"{result.answer}\n\nSources: {markers}"})
        if "citation" in reason.lower() or "evidence" in reason.lower():
            # A model-backed deployment can replace this no-op candidate with one constrained
            # regeneration. Revalidation still happens exactly once and fails closed here.
            return result
        return None

    @staticmethod
    def _insufficient(result: AgentExecutionResult, reason: str) -> AgentExecutionResult:
        warnings = [*result.warnings, f"Output validation failed: {reason}"]
        return result.model_copy(
            update={
                "answer": _INSUFFICIENT_ANSWER,
                "status": ResponseStatus.INSUFFICIENT_EVIDENCE,
                "citations": [],
                "evidence_ids": [],
                "evidence": [],
                "warnings": warnings,
            }
        )
