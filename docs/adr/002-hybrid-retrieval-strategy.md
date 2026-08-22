# ADR 002: Pinecone dense retrieval plus BM25 sparse retrieval

- Status: Accepted
- Date: 2026-08-22

## Context

Internal documents contain semantic concepts as well as exact service names, incident IDs, and
policy terminology. Either dense or keyword retrieval alone will miss useful evidence.

## Decision

Retrieve dense candidates from Pinecone and sparse candidates from a BM25 index asynchronously,
normalize their scores, combine them with default weights 0.65/0.35, deduplicate by chunk ID,
and return typed evidence records. Apply trusted metadata filters before retrieval and keep one
Pinecone namespace for the POC organization. Preserve document and chunk attribution.

## Consequences

Recall improves for both natural-language and exact-match questions. Two indexes must remain
consistent. Sparse-only failure may degrade to marked dense-only results; loss of the authorized
dense store must never lead to an invented answer.

