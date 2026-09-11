from __future__ import annotations

import json
import math
from pathlib import Path

from .models import Chunk, SearchHit


def cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left)) or 1.0
    right_norm = math.sqrt(sum(value * value for value in right)) or 1.0
    return numerator / (left_norm * right_norm)


class LocalVectorStore:
    """Portable JSON vector store used by local demos and tests."""

    def __init__(self, path: Path):
        self.path = path
        self._chunks: dict[str, Chunk] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self._chunks = {item["chunk_id"]: Chunk.from_dict(item) for item in payload}

    def persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [chunk.to_dict() for chunk in sorted(self._chunks.values(), key=lambda item: item.chunk_id)]
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def upsert(self, chunks: list[Chunk]) -> None:
        for chunk in chunks:
            self._chunks[chunk.chunk_id] = chunk

    def delete_document(self, doc_id: str) -> int:
        keys = [key for key, chunk in self._chunks.items() if chunk.doc_id == doc_id]
        for key in keys:
            del self._chunks[key]
        return len(keys)

    def all_chunks(self) -> list[Chunk]:
        return list(self._chunks.values())

    def search(self, query_vector: list[float], k: int = 8) -> list[SearchHit]:
        hits = [
            SearchHit(chunk=chunk, score=cosine_similarity(query_vector, chunk.vector or []), dense_score=cosine_similarity(query_vector, chunk.vector or []))
            for chunk in self._chunks.values()
            if chunk.vector
        ]
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:k]


class MilvusVectorStore:
    """Optional Milvus adapter. LocalVectorStore remains the default for CI."""

    def __init__(self, uri: str, token: str, collection_name: str, dimensions: int):
        try:
            from pymilvus import DataType, MilvusClient
        except ImportError as exc:
            raise RuntimeError("Milvus mode requires pymilvus") from exc
        self.client = MilvusClient(uri=uri, token=token or None)
        self.collection_name = collection_name
        if not self.client.has_collection(collection_name):
            schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
            schema.add_field("doc_id", DataType.VARCHAR, max_length=128)
            schema.add_field("title", DataType.VARCHAR, max_length=512)
            schema.add_field("text", DataType.VARCHAR, max_length=65535)
            schema.add_field("source", DataType.VARCHAR, max_length=1024)
            schema.add_field("chunk_index", DataType.INT64)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dimensions)
            index = self.client.prepare_index_params()
            index.add_index("vector", index_type="AUTOINDEX", metric_type="COSINE")
            self.client.create_collection(collection_name, schema=schema, index_params=index)

    def upsert(self, chunks: list[Chunk]) -> None:
        rows = [
            {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "title": chunk.title,
                "text": chunk.text,
                "source": chunk.source,
                "chunk_index": chunk.chunk_index,
                "vector": chunk.vector,
            }
            for chunk in chunks
        ]
        if rows:
            self.client.upsert(self.collection_name, rows)

    def delete_document(self, doc_id: str) -> int:
        self.client.delete(self.collection_name, filter=f'doc_id == "{doc_id}"')
        return 0

    def persist(self) -> None:
        self.client.flush(self.collection_name)

    def search(self, query_vector: list[float], k: int = 8) -> list[SearchHit]:
        raw = self.client.search(
            self.collection_name,
            data=[query_vector],
            anns_field="vector",
            limit=k,
            output_fields=["chunk_id", "doc_id", "title", "text", "source", "chunk_index"],
            search_params={"metric_type": "COSINE"},
        )[0]
        hits: list[SearchHit] = []
        for item in raw:
            entity = item["entity"]
            chunk_id = entity.get("chunk_id") or item.get("id")
            chunk = Chunk(str(chunk_id), entity["doc_id"], entity["title"], entity["text"], entity["source"], entity["chunk_index"], vector=None)
            hits.append(SearchHit(chunk, float(item["distance"]), dense_score=float(item["distance"])))
        return hits
