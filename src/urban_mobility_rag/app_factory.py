from __future__ import annotations

from .chunking import MetadataAwareChunker
from .config import Settings
from .embeddings import HashEmbeddingProvider, Model2VecEmbeddingProvider, OpenAICompatibleEmbeddingProvider
from .generation import ExtractiveAnswerGenerator, OpenAICompatibleAnswerGenerator
from .indexer import KnowledgeBaseIndexer
from .pipeline import RAGPipeline
from .retrieval import HybridRetriever
from .store import LocalVectorStore, MilvusVectorStore


def build_runtime(settings: Settings, run_index: bool = True) -> tuple[RAGPipeline, LocalVectorStore, object | None]:
    if settings.embedding_backend == "api" and settings.llm_base_url and settings.llm_api_key:
        embeddings = OpenAICompatibleEmbeddingProvider(settings.llm_base_url, settings.llm_api_key, "text-embedding-v3", settings.embedding_dimensions)
    elif settings.embedding_backend == "model2vec":
        embeddings = Model2VecEmbeddingProvider(settings.embedding_model, settings.model_cache_dir or settings.index_dir / "models")
    elif settings.embedding_backend == "hash":
        embeddings = HashEmbeddingProvider(settings.embedding_dimensions)
    else:
        raise ValueError(f"Unsupported or incomplete EMBEDDING_BACKEND: {settings.embedding_backend}")
    store = LocalVectorStore(settings.index_dir / "vectors.json")
    dense_store = store
    if settings.vector_backend == "milvus":
        if not settings.milvus_uri:
            raise ValueError("MILVUS_URI is required when VECTOR_BACKEND=milvus")
        dense_store = MilvusVectorStore(settings.milvus_uri, settings.milvus_token, settings.milvus_collection, embeddings.dimensions)
    report = None
    if run_index:
        indexer = KnowledgeBaseIndexer(
            settings.knowledge_dir,
            settings.index_dir / "manifest.json",
            store,
            embeddings,
            MetadataAwareChunker(settings.chunk_size, settings.chunk_overlap),
            secondary_store=dense_store if dense_store is not store else None,
        )
        report = indexer.run()
    generator = (
        OpenAICompatibleAnswerGenerator(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
        if settings.llm_base_url and settings.llm_api_key
        else ExtractiveAnswerGenerator()
    )
    retriever = HybridRetriever(store.all_chunks(), dense_store, embeddings, settings.dense_weight, settings.sparse_weight)
    return RAGPipeline(retriever, generator=generator, min_evidence_score=settings.min_evidence_score), store, report
