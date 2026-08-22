"""Deterministic section-aware chunking with bounded overlap for oversized sections."""

import hashlib
import re
from collections.abc import Iterable

from orysys_assistant.retrieval.models import ChunkMetadata, DocumentChunk, ParsedDocument


def token_count(text: str) -> int:
    # Stable provider-independent approximation used for chunk budgets.
    return len(re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE))


class SectionAwareChunker:
    def __init__(
        self,
        organization: str,
        *,
        target_tokens: int = 650,
        max_tokens: int = 800,
        overlap_tokens: int = 80,
    ) -> None:
        if not 0 <= overlap_tokens < max_tokens:
            raise ValueError("overlap_tokens must be smaller than max_tokens")
        self._organization = organization
        self._target_tokens = target_tokens
        self._max_tokens = max_tokens
        self._overlap_tokens = overlap_tokens

    def chunk(self, document: ParsedDocument) -> list[DocumentChunk]:
        blocks: list[tuple[str, str]] = []
        for section in document.sections:
            text = f"## {section.heading}\n\n{section.content}"
            if token_count(text) <= self._max_tokens:
                blocks.append((section.heading, text))
            else:
                blocks.extend(self._split_oversized(section.heading, section.content))

        grouped: list[tuple[list[str], list[str]]] = []
        headings: list[str] = []
        contents: list[str] = []
        current_tokens = 0
        for heading, content in blocks:
            size = token_count(content)
            if contents and current_tokens + size > self._target_tokens:
                grouped.append((headings, contents))
                headings, contents, current_tokens = [], [], 0
            headings.append(heading)
            contents.append(content)
            current_tokens += size
        if contents:
            grouped.append((headings, contents))

        chunks = []
        for index, (chunk_headings, chunk_contents) in enumerate(grouped):
            content = "\n\n".join(chunk_contents)
            section_name = " | ".join(dict.fromkeys(chunk_headings))
            chunk_id = hashlib.sha256(
                f"{document.document_id}:{section_name}:{index}".encode()
            ).hexdigest()
            metadata = ChunkMetadata(
                organization=self._organization,
                document_id=document.document_id,
                fixture_id=document.fixture_id,
                chunk_id=chunk_id,
                title=document.title,
                document_type=document.document_type,
                department=document.department,
                access_level=document.access_level,
                created_date=document.created_date,
                source_path=document.source_path,
                checksum=document.checksum,
                section_name=section_name,
                chunk_index=index,
            )
            chunks.append(
                DocumentChunk(content=content, token_count=token_count(content), metadata=metadata)
            )
        return chunks

    def _split_oversized(self, heading: str, content: str) -> Iterable[tuple[str, str]]:
        prefix = f"## {heading}\n\n"
        words = content.split()
        segment_size = self._max_tokens - token_count(prefix)
        step = segment_size - self._overlap_tokens
        for start in range(0, len(words), step):
            segment = words[start : start + segment_size]
            if not segment:
                break
            yield heading, prefix + " ".join(segment)
            if start + segment_size >= len(words):
                break
