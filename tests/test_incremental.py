import tempfile
import unittest
from pathlib import Path

from urban_mobility_rag.chunking import MetadataAwareChunker
from urban_mobility_rag.embeddings import HashEmbeddingProvider
from urban_mobility_rag.indexer import KnowledgeBaseIndexer
from urban_mobility_rag.store import LocalVectorStore


class IncrementalIndexTests(unittest.TestCase):
    def test_unchanged_document_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            knowledge = root / "knowledge"
            knowledge.mkdir()
            (knowledge / "demo.md").write_text("---\ndoc_id: demo\ntitle: 示例\n---\n城市交通知识。", encoding="utf-8")
            store = LocalVectorStore(root / "vectors.json")
            indexer = KnowledgeBaseIndexer(knowledge, root / "manifest.json", store, HashEmbeddingProvider(64), MetadataAwareChunker(80, 10))
            first = indexer.run()
            second = indexer.run()
            self.assertEqual(first.changed_documents, 1)
            self.assertEqual(second.changed_documents, 0)
            self.assertEqual(second.unchanged_documents, 1)


if __name__ == "__main__":
    unittest.main()

