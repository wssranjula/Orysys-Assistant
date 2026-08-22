"""Dense embedding provider seam with deterministic offline implementation."""

import hashlib
import math
import re
from typing import Protocol

from openai import AsyncOpenAI

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "was",
    "were",
    "what",
    "when",
    "which",
    "with",
}


class EmbeddingProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbeddingProvider:
    def __init__(self, api_key: str, model: str, dimension: int) -> None:
        self._client = AsyncOpenAI(api_key=api_key, max_retries=1)
        self._model = model
        self._dimension = dimension

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(
            model=self._model,
            input=texts,
            dimensions=self._dimension,
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in ordered]


class DeterministicHashEmbedding:
    """Normalized hashing-vector encoder for tests; not a production semantic model."""

    def __init__(self, dimension: int = 256) -> None:
        self._dimension = dimension

    @property
    def model_name(self) -> str:
        return "deterministic-hash-embedding"

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            if token in STOPWORDS:
                continue
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimension
            vector[index] += 1
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector
