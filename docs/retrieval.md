# Hybrid Evidence Layer (Phase 3)

## Corpus

The synthetic Commercial Bank corpus contains 48 Markdown documents:

| Category | Count |
|---|---:|
| policies | 7 |
| architecture | 8 |
| runbooks | 8 |
| incidents | 14 |
| product specifications | 5 |
| meeting notes | 6 |

Documents deliberately cross-reference related architecture, runbooks, incidents, specifications,
and reviews. Twelve documents form a Project Orion continuity storyline in which early hypotheses
are superseded, controls reported as complete later fail, and action status changes over time. The
corpus also includes confidential and restricted records for authorization tests and one incident
containing a clearly identified prompt-injection artifact as untrusted evidence.

Each file has validated YAML frontmatter for fixture ID, title, document type, department, access
level, and creation date. The parser rejects unsupported metadata, files outside the corpus root,
missing frontmatter, and documents without indexable sections.

## Deterministic parsing and chunking

```text
document_id = SHA256(lowercase canonical corpus-relative path)
chunk_id    = SHA256(document_id + section names + zero-based chunk index)
checksum    = SHA256(normalized complete source)
```

Line endings and excessive blank lines are normalized. Level-two headings are preserved.
Sections are grouped toward 650 tokens with an 800-token ceiling. An oversized section is split
with 80 tokens of overlap; content from separate documents or access levels is never mixed.
Every chunk stores its real source path, checksum, section, document and chunk IDs, classification,
department, date, and content.

## Ingestion and idempotency

The pipeline parses all documents, chunks them, fits a persistable BM25 vocabulary/IDF model,
generates dense vectors in batches, and upserts both representations under the trusted
`commercial-bank` namespace. Stable chunk IDs make upsert idempotent. The previous manifest is
compared with current IDs and stale chunks are deleted before upsert, handling documents that
shrink or disappear.

`data/ingestion_manifest.json` records source checksums, exact chunk IDs, embedding identity and
dimension, namespace, and the sparse encoder required for query encoding. Re-running unchanged
ingestion produces the same manifest and vector count.

The production adapter uses `PineconeAsyncio` for upsert, delete, dense query, sparse query, and
health operations. The offline adapter uses the same contracts and enforces Pinecone-like
metadata expressions for deterministic tests.

## Trusted retrieval

`RetrievalService.search` requires an immutable backend-created `AccessScope`. It combines the
organization, namespace, allowed access levels, and allowed departments with optional narrowing
filters for department, document type, and date. User filters are placed in an `$and` expression;
they can never replace or broaden the trusted filter.

Dense and sparse candidate queries run concurrently. Positive scores are normalized per channel,
combined with default weights 0.65 dense and 0.35 sparse, deduplicated by real chunk ID, and
limited to the requested evidence count. Evidence includes content, source attribution, raw
component scores, final score, and a deterministic evidence ID.

The `knowledge_search` tool is registered through the central gateway contract. Its input schema
does not contain identity, role, namespace, organization, or access-level fields; attempts to add
those fields are rejected before the retrieval handler runs.

## Evaluation baseline

`data/retrieval_evaluation.json` freezes sixteen questions covering policies, recurring payment
failures, runbooks, restricted records, and malicious retrieved text. With the deterministic
offline dense adapter and BM25 representation, the current baseline is:

| Metric | Result | Target |
|---|---:|---:|
| Recall@5 | 95.00% | at least 80% |
| valid evidence/chunk IDs | 100% | 100% |
| unauthorized chunks | 0 | 0 |

The deterministic hashing encoder exists only for offline repeatability. Pinecone deployments use
the configured OpenAI embedding adapter and should record a provider-specific evaluation before
release.

`data/hard_research_questions.json` adds eight multi-source synthesis challenges. These are not
single-hit retrieval checks: they require reconciling dated claims, tracing control status, mapping
requirements to evidence, or separating authorization recovery from settlement recovery.
