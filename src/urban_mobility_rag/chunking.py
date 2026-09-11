from __future__ import annotations

import hashlib

from .models import Chunk, SourceDocument
from .text import normalize_text, split_sentences


class MetadataAwareChunker:
    def __init__(self, chunk_size: int = 520, chunk_overlap: int = 80):
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split(self, document: SourceDocument) -> list[Chunk]:
        sentences = split_sentences(document.text)
        chunks: list[str] = []
        buffer = ""
        for sentence in sentences:
            separator = "\n" if buffer else ""
            candidate = f"{buffer}{separator}{sentence}"
            if buffer and len(candidate) > self.chunk_size:
                chunks.append(normalize_text(buffer))
                overlap = buffer[-self.chunk_overlap :]
                buffer = f"{overlap}\n{sentence}"
            else:
                buffer = candidate
        if buffer.strip():
            chunks.append(normalize_text(buffer))
        result: list[Chunk] = []
        for index, text in enumerate(chunks):
            stable = hashlib.sha1(f"{document.doc_id}:{index}:{text}".encode("utf-8")).hexdigest()[:16]
            metadata = dict(document.metadata)
            metadata.update({"source_type": document.source_type, "chunk_index": index})
            result.append(Chunk(stable, document.doc_id, document.title, text, document.source, index, metadata))
        return result
