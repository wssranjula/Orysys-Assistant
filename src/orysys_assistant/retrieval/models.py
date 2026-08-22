"""Typed document, chunk, vector, search, and evidence contracts."""

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RetrievalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentSection(RetrievalModel):
    heading: str
    content: str


class ParsedDocument(RetrievalModel):
    document_id: str
    fixture_id: str
    title: str
    document_type: str
    department: str
    access_level: str
    created_date: date
    source_path: str
    checksum: str
    sections: tuple[DocumentSection, ...]


class ChunkMetadata(RetrievalModel):
    organization: str
    document_id: str
    fixture_id: str
    chunk_id: str
    title: str
    document_type: str
    department: str
    access_level: str
    created_date: date
    page_number: int | None = None
    source_path: str
    checksum: str
    section_name: str
    chunk_index: int = Field(ge=0)


class DocumentChunk(RetrievalModel):
    content: str
    token_count: int = Field(gt=0)
    metadata: ChunkMetadata


class SparseVector(RetrievalModel):
    indices: list[int]
    values: list[float]

    @model_validator(mode="after")
    def matching_dimensions(self) -> "SparseVector":
        if len(self.indices) != len(self.values):
            raise ValueError("sparse vector indices and values must have equal length")
        return self


class VectorRecord(RetrievalModel):
    id: str
    values: list[float]
    sparse_values: SparseVector
    metadata: dict[str, str | int | float | bool | list[str]]


class SearchFilters(RetrievalModel):
    department: str | None = None
    document_type: str | None = None
    created_after: date | None = None
    created_before: date | None = None

    @model_validator(mode="after")
    def valid_date_range(self) -> "SearchFilters":
        if self.created_after and self.created_before and self.created_after > self.created_before:
            raise ValueError("created_after must not be after created_before")
        return self


class SearchMatch(RetrievalModel):
    id: str
    score: float
    metadata: dict[str, Any]


class Evidence(RetrievalModel):
    evidence_id: str
    document_id: str
    chunk_id: str
    title: str
    content: str
    page_number: int | None = None
    metadata: dict[str, Any]
    dense_score: float | None = None
    sparse_score: float | None = None
    final_score: float


class IngestionDocumentRecord(RetrievalModel):
    checksum: str
    chunk_ids: list[str]


class IngestionManifest(RetrievalModel):
    schema_version: str = "1.0"
    namespace: str
    embedding_model: str
    embedding_dimension: int
    documents: dict[str, IngestionDocumentRecord]
    sparse_encoder: dict[str, Any]
