"""Chroma-backed knowledge store.

The demo collection uses a deterministic local hash embedding instead of a
downloaded model, so tests and offline CI never reach the network and ranking is
reproducible. Embeddings are supplied explicitly, which also avoids depending on
Chroma's embedding-function protocol.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import chromadb

from tools.schemas import KnowledgeSearchItem, SearchKnowledgeArgs, SearchKnowledgeResult

COLLECTION_NAME = "agentdesk-knowledge"
EMBEDDING_DIMENSIONS = 256
NGRAM_SIZES = (1, 2, 3)
MAX_EXCERPT_CHARS = 1_000


def hash_embedding(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    """Deterministic signed character n-gram feature hashing.

    A plain digest per whole string would be reproducible but carry no lexical
    overlap, so a query could never rank a related chunk above an unrelated one.
    Hashing character n-grams keeps the embedding fully local and offline while
    still giving real term overlap — which is what makes retrieval meaningful for
    the small demo collection.
    """
    vector = [0.0] * dimensions
    normalised = " ".join(text.lower().split())
    for size in NGRAM_SIZES:
        for start in range(max(0, len(normalised) - size + 1)):
            gram = normalised[start : start + size]
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _cosine_distance_to_score(distance: float) -> float:
    return max(0.0, min(1.0, 1.0 - distance))


class ChromaKnowledgeStore:
    """Implements :class:`tools.context.KnowledgeStore` over a local Chroma path."""

    def __init__(self, path: Path, *, collection_name: str = COLLECTION_NAME) -> None:
        self._client = chromadb.PersistentClient(path=str(path))
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def count(self) -> int:
        return int(self._collection.count())

    def reset(self) -> None:
        """Drop and recreate the collection; used by the deterministic seed script."""
        self._client.delete_collection(name=self._collection.name)
        self._collection = self._client.get_or_create_collection(
            name=self._collection.name,
            metadata={"hnsw:space": "cosine"},
        )

    def close(self) -> None:
        """Release the persistent client and its SQLite handles."""
        self._client.close()

    def add_documents(
        self,
        *,
        document_id: str,
        title: str,
        chunks: Sequence[tuple[str, str]],
    ) -> int:
        """Add ``(chunk_id, content)`` pairs for one document."""
        if not chunks:
            return 0
        ids = [chunk_id for chunk_id, _content in chunks]
        documents = [content for _chunk_id, content in chunks]
        metadatas: list[dict[str, Any]] = [
            {"document_id": document_id, "chunk_id": chunk_id, "title": title}
            for chunk_id, _content in chunks
        ]
        self._collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=[hash_embedding(content) for _chunk_id, content in chunks],
        )
        return len(ids)

    async def search(self, args: SearchKnowledgeArgs) -> SearchKnowledgeResult:
        return await asyncio.to_thread(self._search_sync, args)

    def _search_sync(self, args: SearchKnowledgeArgs) -> SearchKnowledgeResult:
        if self.count == 0:
            return SearchKnowledgeResult(refs=[])

        raw = self._collection.query(
            query_embeddings=[hash_embedding(args.query)],
            n_results=min(args.top_k, self.count),
            include=["documents", "metadatas", "distances"],
        )
        documents = _first(raw.get("documents"))
        metadatas = _first(raw.get("metadatas"))
        distances = _first(raw.get("distances"))

        refs: list[KnowledgeSearchItem] = []
        for content, metadata, distance in zip(documents, metadatas, distances, strict=False):
            item = _to_item(content, metadata, distance)
            if item is not None:
                refs.append(item)
        return SearchKnowledgeResult(refs=refs)


def _first(value: object) -> list[Any]:
    if not isinstance(value, list) or not value:
        return []
    inner = value[0]
    return list(inner) if isinstance(inner, list) else []


def _to_item(content: object, metadata: object, distance: object) -> KnowledgeSearchItem | None:
    if not isinstance(metadata, dict):
        return None
    document_id = metadata.get("document_id")
    chunk_id = metadata.get("chunk_id")
    title = metadata.get("title")
    if not isinstance(document_id, str) or not isinstance(chunk_id, str):
        return None
    if not isinstance(title, str):
        return None
    excerpt = content if isinstance(content, str) else ""
    score = _cosine_distance_to_score(float(cast(float, distance)))
    return KnowledgeSearchItem(
        document_id=document_id,
        chunk_id=chunk_id,
        title=title[:200],
        content_excerpt=(excerpt or title)[:MAX_EXCERPT_CHARS],
        score=score,
    )
