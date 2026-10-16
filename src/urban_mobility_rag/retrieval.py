from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .bm25 import BM25Index
from .embeddings import EmbeddingProvider
from .models import Chunk, SearchHit
from .text import tokenize


def metadata_terms(value):
    return value if isinstance(value, list) else [part.strip() for part in str(value or "").split("|") if part.strip()]


def query_contexts(question):
    contexts = []
    if any(t in question for t in ("火车", "铁路", "高铁", "车票", "检票", "身份证", "候车")):
        contexts.append("railway")
    if any(t in question for t in ("地铁", "轨道交通", "换乘", "末班", "站口")):
        contexts.append("public_transit")
    if any(t in question for t in ("步行", "行人", "人行横道", "过马路", "斑马线")):
        contexts.extend(("walking", "road_travel"))
    if any(t in question for t in ("骑行", "自行车", "电动自行车", "非机动车", "共享单车")):
        contexts.extend(("cycling", "road_travel"))
    if any(t in question for t in ("出租车", "打车", "网约车", "上车点", "候客", "拒载", "绕路")):
        contexts.extend(("taxi", "road_travel"))
    if any(t in question for t in ("驾车", "开车", "道路", "封路", "管制", "施工", "停车", "限行")):
        contexts.append("road_travel")
    return list(dict.fromkeys(contexts))


def eligible(chunk, filters):
    if not filters:
        return True
    m = chunk.metadata
    if filters.get("audience") and m.get("audience") != filters["audience"]:
        return False
    if filters.get("data_kind") and m.get("data_kind") != filters["data_kind"]:
        return False
    required_contexts = metadata_terms(m.get("contexts"))
    if required_contexts and not set(required_contexts).intersection(filters.get("contexts", [])):
        return False
    city = m.get("city")
    if city not in (None, "", "通用") and (not filters.get("city") or city.removesuffix("市") != filters["city"].removesuffix("市")):
        return False
    day = filters.get("on_date") or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    if m.get("valid_from") and day < m["valid_from"]:
        return False
    if m.get("valid_until") and day > m["valid_until"]:
        return False
    if m.get("active_after") and m.get("active_before"):
        local_time = filters.get("local_time")
        if not local_time:
            return False
        local_time = local_time[:5]
        start, end = m["active_after"], m["active_before"]
        active = start <= local_time < end if start < end else local_time >= start or local_time < end
        if not active:
            return False
    facility = m.get("facility")
    # An explicit facility-scoped notice is only recalled for that facility; no geometry inference.
    if facility and filters.get("facilities") is not None:
        names = [facility, *metadata_terms(m.get("facility_aliases"))]
        if not any(name in p for name in names for p in filters["facilities"]):
            return False
    return True


class HybridRetriever:
    def __init__(
        self, chunks: list[Chunk], vector_store, embeddings: EmbeddingProvider, dense_weight: float = 0.55, sparse_weight: float = 0.45
    ):
        self.chunks = chunks
        self.vector_store = vector_store
        self.embeddings = embeddings
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight
        self.bm25 = BM25Index(chunks)

    def search(self, query: str, k: int = 8, filters: dict | None = None) -> list[SearchHit]:
        chunks = [c for c in self.chunks if eligible(c, filters)]
        if not chunks:
            return []
        dense = self.vector_store.search(self.embeddings.embed_query(query), k=k * 2, allowed_ids={c.chunk_id for c in chunks})
        sparse = (BM25Index(chunks) if filters else self.bm25).search(query, k=k * 2)
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
