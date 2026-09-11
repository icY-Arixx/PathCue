from __future__ import annotations

import time

from .generation import ExtractiveAnswerGenerator
from .models import QueryResult
from .retrieval import HeuristicReranker, HybridRetriever
from .router import IntentRouter


class RAGPipeline:
    def __init__(self, retriever: HybridRetriever, reranker: HeuristicReranker | None = None, generator=None, router: IntentRouter | None = None, min_evidence_score: float = 0.24):
        self.retriever = retriever
        self.reranker = reranker or HeuristicReranker()
        self.generator = generator or ExtractiveAnswerGenerator()
        self.router = router or IntentRouter()
        self.min_evidence_score = min_evidence_score

    def query(self, question: str, retrieve_k: int = 8, final_k: int = 4) -> QueryResult:
        started = time.perf_counter()
        intent = self.router.route(question)
        trace: list[dict] = [{"node": "route", "intent": intent}]
        candidates = self.retriever.search(question, retrieve_k)
        trace.append({"node": "hybrid_retrieve", "candidate_count": len(candidates)})
        hits = self.reranker.rerank(question, candidates, final_k)
        trace.append({"node": "rerank", "selected_chunk_ids": [hit.chunk.chunk_id for hit in hits]})
        if not hits or hits[0].rerank_score < self.min_evidence_score:
            answer = "知识库中暂未检索到足够证据，请补充问题或更新资料。"
            citations = []
            trace.append({"node": "evidence_gate", "accepted": False, "threshold": self.min_evidence_score})
        else:
            answer, citations = self.generator.generate(question, hits)
            trace.append({"node": "evidence_gate", "accepted": True, "threshold": self.min_evidence_score})
        confidence = round(sum(hit.rerank_score for hit in hits[:2]) / max(1, min(2, len(hits))), 4)
        trace.append({"node": "generate", "citation_count": len(citations)})
        latency = (time.perf_counter() - started) * 1000
        return QueryResult(question, answer, intent, confidence, citations, trace, round(latency, 2))
