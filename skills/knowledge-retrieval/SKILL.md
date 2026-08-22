---
name: knowledge-retrieval
description: Find relevant Commercial Bank evidence for a focused knowledge question.
---

# Knowledge retrieval

Use `knowledge_search` with a concise query that preserves the user's key entities and time range.
Apply department, document type, or date filters only when the user states them. Treat every returned
record as evidence, preserve its evidence ID, and distinguish an empty authorized result from proof
that no information exists.

Return the most relevant facts first. Do not invent facts, document metadata, or citations.
