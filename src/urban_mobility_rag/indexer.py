from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .chunking import MetadataAwareChunker
from .embeddings import EmbeddingProvider
from .loaders import iter_documents


@dataclass(slots=True)
class IndexReport:
    scanned_documents: int = 0
    changed_documents: int = 0
    unchanged_documents: int = 0
    removed_documents: int = 0
    chunks_upserted: int = 0


class KnowledgeBaseIndexer:
    def __init__(
        self,
        knowledge_dir: Path,
        manifest_path: Path,
        store,
        embeddings: EmbeddingProvider,
        chunker: MetadataAwareChunker,
        secondary_store=None,
    ):
        self.knowledge_dir = knowledge_dir
        self.manifest_path = manifest_path
        self.store = store
        self.embeddings = embeddings
        self.chunker = chunker
        self.secondary_store = secondary_store

    def _load_manifest(self) -> dict[str, str]:
        if not self.manifest_path.exists():
            return {}
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def run(self) -> IndexReport:
        previous = self._load_manifest()
        # Preserve a recoverable snapshot before replacing any existing local index.
        if previous:
            backup = self.manifest_path.parent / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.manifest_path, backup / self.manifest_path.name)
            if self.store.path.exists():
                shutil.copy2(self.store.path, backup / self.store.path.name)
        current: dict[str, str] = {}
        report = IndexReport()
        store_identity = type(self.secondary_store or self.store).__name__
        collection_name = getattr(self.secondary_store or self.store, "collection_name", "")
        for document in iter_documents(self.knowledge_dir):
            report.scanned_documents += 1
            digest = str(document.metadata["sha256"])
            fingerprint_source = (
                f"{digest}:{self.chunker.chunk_size}:{self.chunker.chunk_overlap}:"
                f"{type(self.embeddings).__name__}:{self.embeddings.dimensions}:"
                f"{getattr(self.embeddings, 'model_name', getattr(self.embeddings, 'model', 'hash-v1'))}:"
                f"{getattr(self.embeddings, 'revision', 'v1')}:{json.dumps(document.metadata, sort_keys=True, ensure_ascii=False)}:"
                f"{getattr(self.embeddings, 'base_url', '')}:"
                f"{store_identity}:{collection_name}"
            )
            fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
            current[document.doc_id] = fingerprint
            if previous.get(document.doc_id) == fingerprint:
                report.unchanged_documents += 1
                continue
            chunks = self.chunker.split(document)
            vectors = self.embeddings.embed_documents([chunk.text for chunk in chunks])
            if len(vectors) != len(chunks) or any(len(v) != self.embeddings.dimensions for v in vectors):
                raise ValueError("Embedding dimensions/count mismatch; index snapshot preserved")
            for chunk, vector in zip(chunks, vectors):
                chunk.vector = vector
            self.store.delete_document(document.doc_id)
            if self.secondary_store is not None:
                self.secondary_store.delete_document(document.doc_id)
            self.store.upsert(chunks)
            if self.secondary_store is not None:
                self.secondary_store.upsert(chunks)
            report.changed_documents += 1
            report.chunks_upserted += len(chunks)
        for doc_id in previous.keys() - current.keys():
            self.store.delete_document(doc_id)
            if self.secondary_store is not None:
                self.secondary_store.delete_document(doc_id)
            report.removed_documents += 1
        self.store.persist()
        if self.secondary_store is not None:
            self.secondary_store.persist()
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
