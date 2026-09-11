from __future__ import annotations

import json
import statistics
from pathlib import Path

from urban_mobility_rag.app_factory import build_runtime
from urban_mobility_rag.config import Settings


def evaluate() -> dict:
    settings = Settings.from_env()
    pipeline, _, _ = build_runtime(settings)
    dataset_path = settings.project_root / "data" / "evaluation" / "questions.jsonl"
    rows = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    reciprocal_ranks: list[float] = []
    recall_hits = {1: 0, 3: 0, 5: 0, 8: 0}
    route_hits = 0
    citation_hits = 0
    negative_hits = 0
    positive_count = 0
    negative_count = 0
    latencies: list[float] = []
    details: list[dict] = []
    for row in rows:
        candidates = pipeline.retriever.search(row["question"], settings.retrieve_k)
        ids = [hit.chunk.doc_id for hit in candidates]
        expected_doc_id = row.get("expected_doc_id")
        answerable = bool(row.get("is_answerable", True))
        rank = ids.index(expected_doc_id) + 1 if expected_doc_id and expected_doc_id in ids else 0
        if answerable:
            positive_count += 1
            reciprocal_ranks.append(1 / rank if rank else 0.0)
            for cutoff in recall_hits:
                recall_hits[cutoff] += int(bool(rank) and rank <= cutoff)
        else:
            negative_count += 1
        result = pipeline.query(row["question"], settings.retrieve_k, settings.final_k)
        route_hits += int(result.intent == row["expected_intent"])
        if answerable:
            citation_hits += int(any(citation.doc_id == expected_doc_id for citation in result.citations))
        else:
            negative_hits += int(not result.citations)
        latencies.append(result.latency_ms)
        details.append({"id": row["id"], "answerable": answerable, "rank": rank, "intent": result.intent, "confidence": result.confidence, "citation_doc_ids": [item.doc_id for item in result.citations]})
    ordered_latency = sorted(latencies)
    p95_index = max(0, min(len(ordered_latency) - 1, round(0.95 * len(ordered_latency)) - 1))
    return {
        "dataset_size": len(rows),
        "positive_count": positive_count,
        "hard_negative_count": negative_count,
        "configuration": {
            "embedding_backend": settings.embedding_backend,
            "embedding_model": settings.embedding_model if settings.embedding_backend == "model2vec" else None,
            "vector_backend": settings.vector_backend,
            "retrieve_k": settings.retrieve_k,
            "final_k": settings.final_k,
            "min_evidence_score": settings.min_evidence_score,
        },
        "metrics": {
            "recall_at_1": round(recall_hits[1] / max(1, positive_count), 4),
            "recall_at_3": round(recall_hits[3] / max(1, positive_count), 4),
            "recall_at_5": round(recall_hits[5] / max(1, positive_count), 4),
            "recall_at_8": round(recall_hits[8] / max(1, positive_count), 4),
            "mrr_at_8": round(statistics.mean(reciprocal_ranks), 4),
            "route_accuracy": round(route_hits / len(rows), 4),
            "citation_doc_accuracy": round(citation_hits / max(1, positive_count), 4),
            "hard_negative_rejection_rate": round(negative_hits / max(1, negative_count), 4),
            "answerability_accuracy": round((citation_hits + negative_hits) / len(rows), 4),
            "latency_p50_ms": round(statistics.median(latencies), 2),
            "latency_p95_ms": round(ordered_latency[p95_index], 2),
        },
        "details": details,
    }


if __name__ == "__main__":
    report = evaluate()
    output = Path(__file__).resolve().parents[1] / "evaluation_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
