from __future__ import annotations

import hashlib
import statistics
from pathlib import Path

from .chunking import MetadataAwareChunker
from .loaders import iter_documents

REQUIRED_METADATA = ("doc_id", "title", "city", "category", "updated_at")


def analyze_corpus(knowledge_dir: Path, chunker: MetadataAwareChunker) -> dict:
    documents = list(iter_documents(knowledge_dir))
    doc_ids = [document.doc_id for document in documents]
    chunks = [chunk for document in documents for chunk in chunker.split(document)]
    required_cells = len(documents) * len(REQUIRED_METADATA)
    present_cells = 0
    missing_by_field = {field: 0 for field in REQUIRED_METADATA}
    for document in documents:
        values = {"doc_id": document.doc_id, "title": document.title, **document.metadata}
        for field in REQUIRED_METADATA:
            if str(values.get(field, "")).strip():
                present_cells += 1
            else:
                missing_by_field[field] += 1
    lengths = [len(chunk.text) for chunk in chunks]
    content_hashes = [hashlib.sha256(document.text.encode("utf-8")).hexdigest() for document in documents]
    chunk_hashes = [hashlib.sha256(chunk.text.encode("utf-8")).hexdigest() for chunk in chunks]
    return {
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "metadata_completeness": round(present_cells / max(1, required_cells), 4),
        "missing_by_field": missing_by_field,
        "duplicate_doc_id_count": len(doc_ids) - len(set(doc_ids)),
        "duplicate_document_content_count": len(content_hashes) - len(set(content_hashes)),
        "duplicate_chunk_content_count": len(chunk_hashes) - len(set(chunk_hashes)),
        "duplicate_document_rate": round((len(content_hashes) - len(set(content_hashes))) / max(1, len(content_hashes)), 4),
        "empty_document_count": sum(not document.text.strip() for document in documents),
        "chunk_length": {
            "min": min(lengths, default=0),
            "median": round(statistics.median(lengths), 2) if lengths else 0,
            "max": max(lengths, default=0),
        },
    }
