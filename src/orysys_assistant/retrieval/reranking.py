"""Deterministic second-stage reranking for authorized hybrid candidates."""

import re
from typing import Protocol

from orysys_assistant.retrieval.models import Evidence

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_IDENTIFIER_PATTERN = re.compile(r"\b(?:PAY|OR|CG|INC|SVC)-[A-Z0-9-]+\b", re.IGNORECASE)


class Reranker(Protocol):
    """A provider-neutral reranker that never changes the authorized candidate set."""

    def rerank(self, query: str, candidates: list[Evidence], top_k: int) -> list[Evidence]: ...


class HybridLexicalReranker:
    """Blend first-stage rank with exact token and identifier coverage.

    This offline implementation keeps the POC deterministic. A hosted cross-encoder can
    implement the same protocol without changing retrieval or authorization code.
    """

    def __init__(self, lexical_weight: float = 0.25) -> None:
        if not 0 <= lexical_weight <= 1:
            raise ValueError("lexical_weight must be between 0 and 1")
        self._lexical_weight = lexical_weight

    def rerank(self, query: str, candidates: list[Evidence], top_k: int) -> list[Evidence]:
        if top_k <= 0:
            return []
        query_tokens = set(_TOKEN_PATTERN.findall(query.lower()))
        identifiers = {value.upper() for value in _IDENTIFIER_PATTERN.findall(query)}
        rescored: list[Evidence] = []
        for candidate in candidates:
            searchable = f"{candidate.title} {candidate.content}".lower()
            candidate_tokens = set(_TOKEN_PATTERN.findall(searchable))
            token_coverage = (
                len(query_tokens & candidate_tokens) / len(query_tokens) if query_tokens else 0.0
            )
            identifier_coverage = (
                sum(identifier in searchable.upper() for identifier in identifiers)
                / len(identifiers)
                if identifiers
                else 0.0
            )
            lexical_score = 0.8 * token_coverage + 0.2 * identifier_coverage
            rerank_score = (
                1 - self._lexical_weight
            ) * candidate.final_score + self._lexical_weight * lexical_score
            rescored.append(
                candidate.model_copy(
                    update={
                        "final_score": rerank_score,
                        "metadata": {
                            **candidate.metadata,
                            "reranked": True,
                            "first_stage_score": candidate.final_score,
                            "reranker_score": lexical_score,
                        },
                    }
                )
            )
        return sorted(
            rescored,
            key=lambda item: (item.final_score, item.chunk_id),
            reverse=True,
        )[:top_k]
