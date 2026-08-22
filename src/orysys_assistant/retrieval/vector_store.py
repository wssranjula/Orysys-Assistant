"""Vector store protocol, in-memory test store, and asynchronous Pinecone adapter."""

from typing import Any, Protocol, cast

from pinecone import PineconeAsyncio

from orysys_assistant.retrieval.models import (
    SearchMatch,
    SparseVector,
    VectorRecord,
)


class VectorStore(Protocol):
    async def upsert(self, namespace: str, records: list[VectorRecord]) -> int: ...

    async def delete(self, namespace: str, ids: list[str]) -> None: ...

    async def query_dense(
        self,
        namespace: str,
        vector: list[float],
        metadata_filter: dict[str, Any],
        top_k: int,
    ) -> list[SearchMatch]: ...

    async def query_sparse(
        self,
        namespace: str,
        vector: SparseVector,
        metadata_filter: dict[str, Any],
        top_k: int,
    ) -> list[SearchMatch]: ...

    async def ping(self) -> bool: ...

    async def close(self) -> None: ...


def _matches_filter(metadata: dict[str, Any], expression: dict[str, Any]) -> bool:
    if "$and" in expression:
        return all(_matches_filter(metadata, part) for part in expression["$and"])
    for field, condition in expression.items():
        value = metadata.get(field)
        if not isinstance(condition, dict):
            if value != condition:
                return False
            continue
        if "$eq" in condition and value != condition["$eq"]:
            return False
        if "$in" in condition and value not in condition["$in"]:
            return False
        if "$gte" in condition and (value is None or value < condition["$gte"]):
            return False
        if "$lte" in condition and (value is None or value > condition["$lte"]):
            return False
    return True


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._namespaces: dict[str, dict[str, VectorRecord]] = {}

    async def upsert(self, namespace: str, records: list[VectorRecord]) -> int:
        bucket = self._namespaces.setdefault(namespace, {})
        for record in records:
            bucket[record.id] = record
        return len(records)

    async def delete(self, namespace: str, ids: list[str]) -> None:
        bucket = self._namespaces.setdefault(namespace, {})
        for record_id in ids:
            bucket.pop(record_id, None)

    async def query_dense(
        self,
        namespace: str,
        vector: list[float],
        metadata_filter: dict[str, Any],
        top_k: int,
    ) -> list[SearchMatch]:
        matches = []
        for record in self._namespaces.get(namespace, {}).values():
            if _matches_filter(record.metadata, metadata_filter):
                score = sum(left * right for left, right in zip(vector, record.values, strict=True))
                matches.append(SearchMatch(id=record.id, score=score, metadata=record.metadata))
        return sorted(matches, key=lambda match: match.score, reverse=True)[:top_k]

    async def query_sparse(
        self,
        namespace: str,
        vector: SparseVector,
        metadata_filter: dict[str, Any],
        top_k: int,
    ) -> list[SearchMatch]:
        query = dict(zip(vector.indices, vector.values, strict=True))
        matches = []
        for record in self._namespaces.get(namespace, {}).values():
            if not _matches_filter(record.metadata, metadata_filter):
                continue
            document = dict(
                zip(record.sparse_values.indices, record.sparse_values.values, strict=True)
            )
            score = sum(value * document.get(index, 0) for index, value in query.items())
            matches.append(SearchMatch(id=record.id, score=score, metadata=record.metadata))
        return sorted(matches, key=lambda match: match.score, reverse=True)[:top_k]

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    def count(self, namespace: str) -> int:
        return len(self._namespaces.get(namespace, {}))


class PineconeVectorStore:
    def __init__(
        self,
        api_key: str,
        index_name: str,
        dimension: int,
        *,
        host: str | None = None,
    ) -> None:
        self._client = PineconeAsyncio(api_key=api_key)
        self._index_name = index_name
        self._dimension = dimension
        self._host = host
        self._index: Any = None

    async def _get_index(self) -> Any:
        if self._index is None:
            if not self._host:
                description = await self._client.describe_index(self._index_name)
                self._host = str(description.host)
            self._index = self._client.IndexAsyncio(host=self._host)
        return self._index

    async def upsert(self, namespace: str, records: list[VectorRecord]) -> int:
        index = await self._get_index()
        vectors = [
            {
                "id": record.id,
                "values": record.values,
                "sparse_values": record.sparse_values.model_dump(),
                "metadata": record.metadata,
            }
            for record in records
        ]
        response = await index.upsert(vectors=vectors, namespace=namespace, show_progress=False)
        return int(response.upserted_count)

    async def delete(self, namespace: str, ids: list[str]) -> None:
        if ids:
            index = await self._get_index()
            await index.delete(ids=ids, namespace=namespace)

    async def query_dense(
        self,
        namespace: str,
        vector: list[float],
        metadata_filter: dict[str, Any],
        top_k: int,
    ) -> list[SearchMatch]:
        index = await self._get_index()
        response = await index.query(
            namespace=namespace,
            vector=vector,
            filter=metadata_filter,
            top_k=top_k,
            include_metadata=True,
        )
        return self._convert_matches(response.matches)

    async def query_sparse(
        self,
        namespace: str,
        vector: SparseVector,
        metadata_filter: dict[str, Any],
        top_k: int,
    ) -> list[SearchMatch]:
        index = await self._get_index()
        response = await index.query(
            namespace=namespace,
            vector=[0.0] * self._dimension,
            sparse_vector=vector.model_dump(),
            filter=metadata_filter,
            top_k=top_k,
            include_metadata=True,
        )
        return self._convert_matches(response.matches)

    @staticmethod
    def _convert_matches(matches: Any) -> list[SearchMatch]:
        return [
            SearchMatch(
                id=str(match.id),
                score=float(match.score),
                metadata=cast(dict[str, Any], match.metadata or {}),
            )
            for match in matches
        ]

    async def ping(self) -> bool:
        await self._client.describe_index(self._index_name)
        return True

    async def close(self) -> None:
        if self._index is not None:
            await self._index.close()
        await self._client.close()  # type: ignore[no-untyped-call]
