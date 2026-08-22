"""Markdown frontmatter parsing and content normalization."""

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from orysys_assistant.domain.errors import InvalidRequestError
from orysys_assistant.retrieval.models import DocumentSection, ParsedDocument

REQUIRED_FRONTMATTER = {
    "fixture_id",
    "title",
    "document_type",
    "department",
    "access_level",
    "created_date",
}
ALLOWED_DOCUMENT_TYPES = {
    "policy",
    "architecture",
    "runbook",
    "incident",
    "product_specification",
    "meeting_note",
}
ALLOWED_ACCESS_LEVELS = {"internal", "confidential", "restricted"}


def normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in value.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class MarkdownDocumentParser:
    def __init__(self, corpus_root: Path) -> None:
        self._corpus_root = corpus_root.resolve()

    def parse(self, path: Path) -> ParsedDocument:
        resolved = path.resolve()
        try:
            source_path = resolved.relative_to(self._corpus_root).as_posix()
        except ValueError as exc:
            raise InvalidRequestError("Document path is outside the configured corpus.") from exc

        raw = normalize_text(resolved.read_text(encoding="utf-8"))
        frontmatter, markdown = self._split_frontmatter(raw)
        missing = REQUIRED_FRONTMATTER - set(frontmatter)
        if missing:
            raise InvalidRequestError(
                "Document metadata is incomplete.", details={"fields": sorted(missing)}
            )

        document_type = str(frontmatter["document_type"])
        access_level = str(frontmatter["access_level"])
        if document_type not in ALLOWED_DOCUMENT_TYPES:
            raise InvalidRequestError("Document type is not supported.")
        if access_level not in ALLOWED_ACCESS_LEVELS:
            raise InvalidRequestError("Document access level is not supported.")

        sections = self._sections(markdown)
        canonical_path = source_path.lower()
        document_id = hashlib.sha256(canonical_path.encode("utf-8")).hexdigest()
        checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        created = frontmatter["created_date"]
        if not isinstance(created, date):
            created = date.fromisoformat(str(created))

        return ParsedDocument(
            document_id=document_id,
            fixture_id=str(frontmatter["fixture_id"]),
            title=str(frontmatter["title"]),
            document_type=document_type,
            department=str(frontmatter["department"]),
            access_level=access_level,
            created_date=created,
            source_path=source_path,
            checksum=checksum,
            sections=tuple(sections),
        )

    @staticmethod
    def _split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
        match = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, flags=re.DOTALL)
        if match is None:
            raise InvalidRequestError("Markdown document is missing YAML frontmatter.")
        loaded = yaml.safe_load(match.group(1))
        if not isinstance(loaded, dict):
            raise InvalidRequestError("Document frontmatter must be a mapping.")
        return loaded, normalize_text(match.group(2))

    @staticmethod
    def _sections(markdown: str) -> list[DocumentSection]:
        sections: list[DocumentSection] = []
        heading = "Document"
        body: list[str] = []
        for line in markdown.splitlines():
            if line.startswith("## "):
                if body and normalize_text("\n".join(body)):
                    sections.append(
                        DocumentSection(heading=heading, content=normalize_text("\n".join(body)))
                    )
                heading = line.removeprefix("## ").strip()
                body = []
            elif not line.startswith("# "):
                body.append(line)
        if body and normalize_text("\n".join(body)):
            sections.append(
                DocumentSection(heading=heading, content=normalize_text("\n".join(body)))
            )
        if not sections:
            raise InvalidRequestError("Document contains no indexable sections.")
        return sections
