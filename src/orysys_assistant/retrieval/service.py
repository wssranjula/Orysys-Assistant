"""Async trusted-scope dense, sparse, and hybrid evidence retrieval."""

import asyncio
import hashlib
from typing import Any, cast

from langsmith import traceable

from orysys_assistant.domain.errors import RetrievalUnavailableError
from orysys_assistant.retrieval.embeddings import EmbeddingProvider
from orysys_assistant.retrieval.models import Evidence, SearchFilters, SearchMatch
from orysys_assistant.retrieval.sparse_encoding import BM25SparseEncoder
from orysys_assistant.retrieval.vector_store import VectorStore
from orysys_assistant.security.models import AccessScope


def _normalize(matches: list[SearchMatch]) -> dict[str, float]:
    positive = [match for match in matches if match.score > 0]
    if not positive:
        return {}
    low = min(match.score for match in positive)
    high = max(match.score for match in positive)
    if high == low:
        return {match.id: 1.0 for match in positive}
    return {match.id: (match.score - low) / (high - low) for match in positive}


class RetrievalService:
    def __init__(
        self,
        *,
        vector_store: VectorStore,
        embeddings: EmbeddingProvider,
        sparse_encoder: BM25SparseEncoder,
        dense_weight: float = 0.65,
        sparse_weight: float = 0.35,
        candidate_count: int = 20,
    ) -> None:
        if abs(dense_weight + sparse_weight - 1) > 1e-9:
            raise ValueError("dense and sparse weights must sum to 1")
        self._vector_store = vector_store
        self._embeddings = embeddings
        self._sparse_encoder = sparse_encoder
        self._dense_weight = dense_weight
        self._sparse_weight = sparse_weight
        self._candidate_count = candidate_count

    @traceable(name="hybrid-knowledge-retrieval", run_type="retriever")
    async def search(
        self,
        query: str,
        access_scope: AccessScope,
        filters: SearchFilters | None = None,
        top_k: int = 6,
    ) -> list[Evidence]:
        filters = filters or SearchFilters()
        metadata_filter = self._metadata_filter(access_scope, filters)
        try:
            dense_vector = (await self._embeddings.embed_texts([query]))[0]
        except Exception as exc:
            raise RetrievalUnavailableError(
                "The retrieval service is temporarily unavailable."
            ) from exc
        sparse_vector = self._sparse_encoder.encode_query(query)
        dense_result, sparse_result = await asyncio.gather(
            self._vector_store.query_dense(
                access_scope.namespace,
                dense_vector,
                metadata_filter,
                self._candidate_count,
            ),
            self._vector_store.query_sparse(
                access_scope.namespace,
                sparse_vector,
                metadata_filter,
                self._candidate_count,
            ),
            return_exceptions=True,
        )
        if isinstance(dense_result, BaseException):
            raise RetrievalUnavailableError(
                "The retrieval service is temporarily unavailable."
            ) from dense_result
        sparse_degraded = isinstance(sparse_result, BaseException)
        dense_matches = dense_result
        sparse_matches = [] if sparse_degraded else cast(list[SearchMatch], sparse_result)
        evidence = self._combine(dense_matches, sparse_matches, top_k)
        if sparse_degraded:
            evidence = [
                item.model_copy(
                    update={
                        "metadata": {
                            **item.metadata,
                            "retrieval_mode": "dense_only",
                            "retrieval_degraded": True,
                        }
                    }
                )
                for item in evidence
            ]
        return evidence

    @staticmethod
    def _metadata_filter(
        access_scope: AccessScope,
        filters: SearchFilters,
    ) -> dict[str, Any]:
        expressions: list[dict[str, Any]] = [access_scope.retrieval_filter()]
        if filters.department:
            expressions.append({"department": {"$eq": filters.department}})
        if filters.document_type:
            expressions.append({"document_type": {"$eq": filters.document_type}})
        if filters.created_after:
            expressions.append({"created_date": {"$gte": filters.created_after.isoformat()}})
        if filters.created_before:
            expressions.append({"created_date": {"$lte": filters.created_before.isoformat()}})
        return expressions[0] if len(expressions) == 1 else {"$and": expressions}

    def _combine(
        self,
        dense_matches: list[SearchMatch],
        sparse_matches: list[SearchMatch],
        top_k: int,
    ) -> list[Evidence]:
        dense_normalized = _normalize(dense_matches)
        sparse_normalized = _normalize(sparse_matches)
        dense_by_id = {match.id: match for match in dense_matches}
        sparse_by_id = {match.id: match for match in sparse_matches}
        candidate_ids = dense_normalized.keys() | sparse_normalized.keys()
        ranked = sorted(
            candidate_ids,
            key=lambda record_id: (
                self._dense_weight * dense_normalized.get(record_id, 0)
                + self._sparse_weight * sparse_normalized.get(record_id, 0)
            ),
            reverse=True,
        )[:top_k]

        evidence = []
        for record_id in ranked:
            dense_match = dense_by_id.get(record_id)
            sparse_match = sparse_by_id.get(record_id)
            match = dense_match or sparse_match
            if match is None:
                continue
            metadata = dict(match.metadata)
            content = str(metadata.pop("content"))
            page_number = int(metadata.get("page_number", -1))
            final_score = self._dense_weight * dense_normalized.get(
                record_id, 0
            ) + self._sparse_weight * sparse_normalized.get(record_id, 0)
            evidence.append(
                Evidence(
                    evidence_id=(
                        "ev_" + hashlib.sha256(f"{record_id}:{content}".encode()).hexdigest()[:20]
                    ),
                    document_id=str(metadata["document_id"]),
                    chunk_id=record_id,
                    title=str(metadata["title"]),
                    content=content,
                    page_number=None if page_number < 0 else page_number,
                    metadata=metadata,
                    dense_score=dense_match.score if dense_match else None,
                    sparse_score=sparse_match.score if sparse_match else None,
                    final_score=final_score,
                )
            )
        return evidence
