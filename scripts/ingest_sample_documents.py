"""Ingest the synthetic corpus into Pinecone or run the full pipeline in memory."""

import argparse
import asyncio
import json
from pathlib import Path

from orysys_assistant.config import Settings
from orysys_assistant.retrieval.chunking import SectionAwareChunker
from orysys_assistant.retrieval.embeddings import (
    DeterministicHashEmbedding,
    OpenAIEmbeddingProvider,
)
from orysys_assistant.retrieval.ingestion import IngestionPipeline
from orysys_assistant.retrieval.parsing import MarkdownDocumentParser
from orysys_assistant.retrieval.vector_store import InMemoryVectorStore, PineconeVectorStore

ROOT = Path(__file__).resolve().parents[1]


async def ingest(backend: str) -> None:
    settings = Settings()
    corpus_root = ROOT / "data" / "sample_documents"
    manifest_path = ROOT / "data" / "ingestion_manifest.json"

    if backend == "pinecone":
        if not settings.pinecone_api_key or not settings.openai_api_key:
            raise SystemExit("PINECONE_API_KEY and OPENAI_API_KEY are required for Pinecone")
        embeddings = OpenAIEmbeddingProvider(
            settings.openai_api_key,
            settings.embedding_model,
            settings.embedding_dimension,
        )
        store = PineconeVectorStore(
            settings.pinecone_api_key,
            settings.pinecone_index,
            settings.embedding_dimension,
            host=settings.pinecone_host,
        )
    else:
        embeddings = DeterministicHashEmbedding(dimension=256)
        store = InMemoryVectorStore()

    pipeline = IngestionPipeline(
        corpus_root=corpus_root,
        manifest_path=manifest_path,
        namespace=settings.pinecone_namespace,
        parser=MarkdownDocumentParser(corpus_root),
        chunker=SectionAwareChunker(settings.organization_id),
        embeddings=embeddings,
        vector_store=store,
    )
    try:
        result = await pipeline.run()
    finally:
        await store.close()
    print(
        json.dumps(
            {
                "backend": backend,
                "documents": result.documents,
                "chunks": result.chunks,
                "upserted": result.upserted,
                "deleted_stale": result.deleted_stale,
                "manifest": str(result.manifest_path),
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("memory", "pinecone"), default="memory")
    args = parser.parse_args()
    asyncio.run(ingest(args.backend))


if __name__ == "__main__":
    main()
