"""Persistable BM25 sparse vector representation for documents and queries."""

import math
import re
from collections import Counter
from typing import Any

from orysys_assistant.retrieval.models import SparseVector


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class BM25SparseEncoder:
    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._vocabulary: dict[str, int] = {}
        self._idf: dict[str, float] = {}
        self._average_document_length = 0.0
        self._document_count = 0

    def fit(self, documents: list[str]) -> None:
        tokenized = [tokenize(document) for document in documents]
        self._document_count = len(tokenized)
        self._average_document_length = (
            sum(len(tokens) for tokens in tokenized) / len(tokenized) if tokenized else 0
        )
        document_frequency: Counter[str] = Counter()
        for tokens in tokenized:
            document_frequency.update(set(tokens))
        terms = sorted(document_frequency)
        self._vocabulary = {term: index for index, term in enumerate(terms)}
        self._idf = {
            term: math.log(1 + (self._document_count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def encode_document(self, text: str) -> SparseVector:
        self._require_fit()
        tokens = tokenize(text)
        frequencies = Counter(tokens)
        length = len(tokens)
        values: list[tuple[int, float]] = []
        for term, frequency in frequencies.items():
            if term not in self._vocabulary:
                continue
            denominator = frequency + self._k1 * (
                1 - self._b + self._b * length / max(self._average_document_length, 1)
            )
            score = self._idf[term] * (frequency * (self._k1 + 1)) / denominator
            values.append((self._vocabulary[term], score))
        values.sort()
        return SparseVector(
            indices=[index for index, _ in values],
            values=[score for _, score in values],
        )

    def encode_query(self, text: str) -> SparseVector:
        self._require_fit()
        frequencies = Counter(tokenize(text))
        values = sorted(
            (
                self._vocabulary[term],
                self._idf[term] * (1 + math.log(frequency)),
            )
            for term, frequency in frequencies.items()
            if term in self._vocabulary
        )
        return SparseVector(
            indices=[index for index, _ in values],
            values=[score for _, score in values],
        )

    def to_dict(self) -> dict[str, Any]:
        self._require_fit()
        return {
            "k1": self._k1,
            "b": self._b,
            "vocabulary": self._vocabulary,
            "idf": self._idf,
            "average_document_length": self._average_document_length,
            "document_count": self._document_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BM25SparseEncoder":
        encoder = cls(k1=float(data["k1"]), b=float(data["b"]))
        encoder._vocabulary = {str(key): int(value) for key, value in data["vocabulary"].items()}
        encoder._idf = {str(key): float(value) for key, value in data["idf"].items()}
        encoder._average_document_length = float(data["average_document_length"])
        encoder._document_count = int(data["document_count"])
        return encoder

    def _require_fit(self) -> None:
        if not self._vocabulary:
            raise RuntimeError("BM25 sparse encoder must be fitted before use")
