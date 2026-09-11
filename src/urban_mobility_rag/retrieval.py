from __future__ import annotations

from .bm25 import BM25Index
from .embeddings import EmbeddingProvider
from .models import Chunk, SearchHit
from .text import tokenize


class HybridRetriever:
    def __init__(self, chunks: list[Chunk], vector_store, embeddings: EmbeddingProvider, dense_weight: float = 0.55, sparse_weight: float = 0.45):
        self.chunks = chunks
        self.vector_store = vector_store
        self.embeddings = embeddings
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight
        self.bm25 = BM25Index(chunks)

    def search(self, query: str, k: int = 8) -> list[SearchHit]:
        dense = self.vector_store.search(self.embeddings.embed_query(query), k=k * 2)
        sparse = self.bm25.search(query, k=k * 2)
        combined: dict[str, SearchHit] = {}
        for rank, hit in enumerate(dense, 1):
            item = combined.setdefault(hit.chunk.chunk_id, SearchHit(hit.chunk, 0.0))
            item.dense_score = hit.dense_score
            item.score += self.dense_weight / (60 + rank)
        for rank, hit in enumerate(sparse, 1):
            item = combined.setdefault(hit.chunk.chunk_id, SearchHit(hit.chunk, 0.0))
            item.sparse_score = hit.sparse_score
            item.score += self.sparse_weight / (60 + rank)
        return sorted(combined.values(), key=lambda hit: hit.score, reverse=True)[:k]


class HeuristicReranker:
    """Transparent reranker combining token coverage, title match and retrieval rank."""

    def rerank(self, query: str, hits: list[SearchHit], k: int = 4) -> list[SearchHit]:
        # Single Chinese characters create accidental overlap for unrelated topics;
        # use bigrams/words for the evidence score while BM25 still keeps full recall.
        query_tokens = {token for token in tokenize(query) if len(token) > 1}
        for hit in hits:
            text_tokens = {token for token in tokenize(hit.chunk.text) if len(token) > 1}
            title_tokens = {token for token in tokenize(hit.chunk.title) if len(token) > 1}
            coverage = len(query_tokens & text_tokens) / max(1, len(query_tokens))
            title_match = len(query_tokens & title_tokens) / max(1, len(query_tokens))
            hit.rerank_score = 0.55 * coverage + 0.25 * title_match + 0.20 * min(hit.score * 70, 1.0)
        return sorted(hits, key=lambda hit: hit.rerank_score, reverse=True)[:k]
