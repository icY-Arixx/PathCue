import tempfile
import unittest
from pathlib import Path

from urban_mobility_rag.embeddings import HashEmbeddingProvider
from urban_mobility_rag.models import Chunk
from urban_mobility_rag.retrieval import HeuristicReranker, HybridRetriever
from urban_mobility_rag.store import LocalVectorStore


class RetrievalTests(unittest.TestCase):
    def test_hybrid_retrieval_returns_relevant_document(self):
        with tempfile.TemporaryDirectory() as directory:
            embeddings = HashEmbeddingProvider(128)
            chunks = [
                Chunk("1", "cycling", "骑行安全", "夜间骑行应检查前灯、尾灯和刹车。", "a.md", 0),
                Chunk("2", "parking", "停车指南", "停车场应检查限高和收费规则。", "b.md", 0),
            ]
            vectors = embeddings.embed_documents([chunk.text for chunk in chunks])
            for chunk, vector in zip(chunks, vectors):
                chunk.vector = vector
            store = LocalVectorStore(Path(directory) / "vectors.json")
            store.upsert(chunks)
            retriever = HybridRetriever(chunks, store, embeddings)
            hits = HeuristicReranker().rerank("夜间骑车要检查什么", retriever.search("夜间骑车要检查什么"), 1)
            self.assertEqual(hits[0].chunk.doc_id, "cycling")


if __name__ == "__main__":
    unittest.main()

