"""Idempotent parse-to-vector ingestion pipeline with a persisted manifest."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from orysys_assistant.retrieval.chunking import SectionAwareChunker
from orysys_assistant.retrieval.embeddings import EmbeddingProvider
from orysys_assistant.retrieval.models import (
    DocumentChunk,
    IngestionDocumentRecord,
    IngestionManifest,
    VectorRecord,
)
from orysys_assistant.retrieval.parsing import MarkdownDocumentParser
from orysys_assistant.retrieval.sparse_encoding import BM25SparseEncoder
from orysys_assistant.retrieval.vector_store import VectorStore


@dataclass(frozen=True, slots=True)
class IngestionResult:
    documents: int
    chunks: int
    upserted: int
    deleted_stale: int
    manifest_path: Path


T = TypeVar("T")


def batched(values: list[T], size: int) -> Iterable[list[T]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


class IngestionPipeline:
    def __init__(
        self,
        *,
        corpus_root: Path,
        manifest_path: Path,
        namespace: str,
        parser: MarkdownDocumentParser,
        chunker: SectionAwareChunker,
        embeddings: EmbeddingProvider,
        vector_store: VectorStore,
        batch_size: int = 32,
    ) -> None:
        self._corpus_root = corpus_root
        self._manifest_path = manifest_path
        self._namespace = namespace
        self._parser = parser
        self._chunker = chunker
        self._embeddings = embeddings
        self._vector_store = vector_store
        self._batch_size = batch_size

    async def run(self) -> IngestionResult:
        documents = [self._parser.parse(path) for path in sorted(self._corpus_root.rglob("*.md"))]
        chunks = [chunk for document in documents for chunk in self._chunker.chunk(document)]
        sparse_encoder = BM25SparseEncoder()
        sparse_encoder.fit([chunk.content for chunk in chunks])

        old_manifest = self._load_manifest()
        current_ids = {chunk.metadata.chunk_id for chunk in chunks}
        previous_ids = (
            {
                chunk_id
                for record in old_manifest.documents.values()
                for chunk_id in record.chunk_ids
            }
            if old_manifest
            else set()
        )
        stale_ids = sorted(previous_ids - current_ids)
        await self._vector_store.delete(self._namespace, stale_ids)

        upserted = 0
        for chunk_batch in batched(chunks, self._batch_size):
            dense_vectors = await self._embeddings.embed_texts(
                [chunk.content for chunk in chunk_batch]
            )
            records = [
                self._vector_record(chunk, dense, sparse_encoder)
                for chunk, dense in zip(chunk_batch, dense_vectors, strict=True)
            ]
            upserted += await self._vector_store.upsert(self._namespace, records)

        by_document: dict[str, list[DocumentChunk]] = {}
        for chunk in chunks:
            by_document.setdefault(chunk.metadata.document_id, []).append(chunk)
        manifest = IngestionManifest(
            namespace=self._namespace,
            embedding_model=self._embeddings.model_name,
            embedding_dimension=self._embeddings.dimension,
            documents={
                document.document_id: IngestionDocumentRecord(
                    checksum=document.checksum,
                    chunk_ids=[
                        chunk.metadata.chunk_id for chunk in by_document[document.document_id]
                    ],
                )
                for document in documents
            },
            sparse_encoder=sparse_encoder.to_dict(),
        )
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self._manifest_path.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return IngestionResult(
            documents=len(documents),
            chunks=len(chunks),
            upserted=upserted,
            deleted_stale=len(stale_ids),
            manifest_path=self._manifest_path,
        )

    def _load_manifest(self) -> IngestionManifest | None:
        if not self._manifest_path.exists():
            return None
        return IngestionManifest.model_validate_json(
            self._manifest_path.read_text(encoding="utf-8")
        )

    @staticmethod
    def _vector_record(
        chunk: DocumentChunk,
        dense_vector: list[float],
        sparse_encoder: BM25SparseEncoder,
    ) -> VectorRecord:
        metadata = chunk.metadata.model_dump(mode="json")
        # Pinecone supports range operators only for numeric metadata. Keep the ISO
        # value for display/citations and use the ordinal exclusively for filtering.
        metadata["created_date_ordinal"] = chunk.metadata.created_date.toordinal()
        metadata["page_number"] = (
            chunk.metadata.page_number if chunk.metadata.page_number is not None else -1
        )
        metadata["content"] = chunk.content
        metadata["token_count"] = chunk.token_count
        return VectorRecord(
            id=chunk.metadata.chunk_id,
            values=dense_vector,
            sparse_values=sparse_encoder.encode_document(chunk.content),
            metadata=metadata,
        )
