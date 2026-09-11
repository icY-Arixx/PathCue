from __future__ import annotations

import json
from pathlib import Path

from urban_mobility_rag.embeddings import Model2VecEmbeddingProvider
from urban_mobility_rag.store import cosine_similarity


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    provider = Model2VecEmbeddingProvider("minishlab/potion-multilingual-128M", root / "data" / "models")
    query = provider.embed_query("道路施工期间应该怎样绕行？")
    relevant = provider.embed_query("道路封闭后需要发布绕行路线和施工时间。")
    irrelevant = provider.embed_query("餐厅菜单包含甜点和饮料。")
    payload = {
        "provider": "Model2Vec",
        "model": provider.model_name,
        "dimensions": provider.dimensions,
        "relevant_cosine_similarity": round(cosine_similarity(query, relevant), 6),
        "irrelevant_cosine_similarity": round(cosine_similarity(query, irrelevant), 6),
    }
    output = root / "artifacts" / "embedding_smoke.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
