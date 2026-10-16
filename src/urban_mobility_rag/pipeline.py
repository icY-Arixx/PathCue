from __future__ import annotations

import re
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

    def query(self, question: str, retrieve_k: int = 8, final_k: int = 4, filters: dict | None = None) -> QueryResult:
        started = time.perf_counter()
        intent = self.router.route(question)
        trace: list[dict] = [{"node": "route", "intent": intent}]
        if filters and filters.get("data_kind") == "real" and re.search(
            r"(?:实时|此刻|现在|今天).{0,16}(?:排队|拥挤|积水|电梯.{0,4}(?:坏|停|正常)|停运|封闭|封路|交通管制|施工|限行|停车)",
            question,
        ):
            return QueryResult(
                question,
                "现有知识库不能核实当前道路、排队、设施或运营状态，需要联网查找最新来源或现场确认。",
                intent,
                0,
                [],
                trace + [{"node": "live_evidence_required", "available": False}],
                round((time.perf_counter() - started) * 1000, 2),
            )
        candidates = self.retriever.search(question, retrieve_k, filters=filters)
        trace.append({"node": "hybrid_retrieve", "candidate_count": len(candidates)})
        hits = self.reranker.rerank(question, candidates, final_k)
        versions: dict[str, set[str]] = {}
        for hit in hits:
            metadata = hit.chunk.metadata
            scope = (metadata.get("source_url"), metadata.get("facility"), metadata.get("claim_scope") or metadata.get("facility_area")) if metadata.get("source_url") else metadata.get("facility")
            if scope and metadata.get("version"):
                versions.setdefault(scope, set()).add(metadata["version"])
        conflicting_versions = any(len(v) > 1 for v in versions.values())
        trace.append({"node": "rerank", "selected_chunk_ids": [hit.chunk.chunk_id for hit in hits]})
        if not hits or hits[0].rerank_score < self.min_evidence_score:
            answer = "知识库中暂未检索到足够证据，请补充问题或更新资料。"
            citations = []
            trace.append({"node": "evidence_gate", "accepted": False, "threshold": self.min_evidence_score})
        else:
            answer, citations = self.generator.generate(question, hits)
            if conflicting_versions:
                answer = "召回到适用范围相同的多个资料版本，内容可能冲突，请核对原始来源。" + answer
                trace.append({"node": "version_conflict", "requires_verification": True})
            trace.append({"node": "evidence_gate", "accepted": True, "threshold": self.min_evidence_score})
        confidence = round(sum(hit.rerank_score for hit in hits[:2]) / max(1, min(2, len(hits))), 4)
        trace.append({"node": "generate", "citation_count": len(citations)})
        latency = (time.perf_counter() - started) * 1000
        return QueryResult(question, answer, intent, confidence, citations, trace, round(latency, 2))
