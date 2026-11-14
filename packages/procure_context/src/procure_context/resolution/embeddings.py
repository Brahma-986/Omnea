"""Stage 3 Embedding Provider: pgvector 384-dim semantic retrieval with outage degradation."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Any, Protocol


def cosine_similarity(v1: Sequence[float], v2: Sequence[float]) -> float:
    """Calculate cosine similarity between two unit vectors."""
    if len(v1) != len(v2) or not v1:
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2, strict=False))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm1 * norm2)))


class EmbeddingProvider(Protocol):
    """Protocol for embedding providers."""

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate 384-dimensional unit embeddings for given texts."""
        ...


class HashEmbeddingProvider:
    """Deterministic character-trigram projection into R^384 for fast, offline testing."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self._cache: dict[str, list[float]] = {}

    def _embed_single(self, text: str) -> list[float]:
        t = text.strip().lower()
        if t in self._cache:
            return self._cache[t]

        vec = [0.0] * self.dim
        # Project character trigrams via MD5 hashing
        padded = f"  {t}  "
        trigrams = [padded[i : i + 3] for i in range(len(padded) - 2)]
        if not trigrams:
            trigrams = [t]

        for tri in trigrams:
            digest = hashlib.md5(tri.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            val = ((int.from_bytes(digest[4:6], "big") % 1000) / 500.0) - 1.0
            vec[idx] += val

        # Normalize to unit vector
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0.0:
            vec = [x / norm for x in vec]
        else:
            vec[0] = 1.0

        self._cache[t] = vec
        return vec

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts deterministically."""
        return [self._embed_single(text) for text in texts]


class FailingEmbeddingProvider:
    """Simulates an embedding API outage for resilience and degradation testing."""

    async def embed_texts(self, _texts: list[str]) -> list[list[float]]:
        raise ConnectionError("Embedding service unreachable (HTTP 503 Outage)")


class RealEmbeddingProvider:
    """Real 384-dimensional dense semantic embedding provider using all-MiniLM-L6-v2."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        self._model: Any = None
        self._cache: dict[str, list[float]] = {}

    def _get_model(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def _embed_single(self, text: str) -> list[float]:
        t = text.strip()
        if not t:
            return [0.0] * 384
        if t in self._cache:
            return self._cache[t]
        model = self._get_model()
        embs = list(model.embed([t]))
        if not embs:
            return [0.0] * 384
        vec = [float(x) for x in embs[0]]
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0.0:
            vec = [x / norm for x in vec]
        self._cache[t] = vec
        return vec

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate 384-dim dense embeddings."""
        if not texts:
            return []

        results: list[list[float]] = []
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        for i, t in enumerate(texts):
            clean = t.strip()
            if not clean:
                results.append([0.0] * 384)
            elif clean in self._cache:
                results.append(self._cache[clean])
            else:
                results.append([])
                uncached_indices.append(i)
                uncached_texts.append(clean)

        if uncached_texts:
            model = self._get_model()
            embeddings_iter = model.embed(uncached_texts)
            for idx, emb in zip(uncached_indices, embeddings_iter, strict=False):
                emb_list = [float(x) for x in emb]
                norm = math.sqrt(sum(x * x for x in emb_list))
                if norm > 0.0:
                    emb_list = [x / norm for x in emb_list]
                self._cache[texts[idx].strip()] = emb_list
                results[idx] = emb_list

        return results
