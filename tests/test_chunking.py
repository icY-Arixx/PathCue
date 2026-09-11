import unittest

from urban_mobility_rag.chunking import MetadataAwareChunker
from urban_mobility_rag.models import SourceDocument


class ChunkingTests(unittest.TestCase):
    def test_metadata_and_ids_are_preserved(self):
        document = SourceDocument("doc", "标题", "第一句。第二句。第三句。", "demo.md", "md", {"city": "武汉"})
        chunks = MetadataAwareChunker(chunk_size=10, chunk_overlap=2).split(document)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.doc_id == "doc" for chunk in chunks))
        self.assertTrue(all(chunk.metadata["city"] == "武汉" for chunk in chunks))
        self.assertEqual(len({chunk.chunk_id for chunk in chunks}), len(chunks))


if __name__ == "__main__":
    unittest.main()

