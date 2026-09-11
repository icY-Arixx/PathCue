from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    knowledge_dir: Path
    index_dir: Path
    feedback_path: Path
    embedding_backend: str = "hash"
    embedding_model: str = "minishlab/potion-multilingual-128M"
    model_cache_dir: Path | None = None
    vector_backend: str = "local"
    embedding_dimensions: int = 384
    chunk_size: int = 520
    chunk_overlap: int = 80
    dense_weight: float = 0.55
    sparse_weight: float = 0.45
    retrieve_k: int = 8
    final_k: int = 4
    min_evidence_score: float = 0.30
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "qwen-plus"
    amap_api_key: str = ""
    milvus_uri: str = ""
    milvus_token: str = ""
    milvus_collection: str = "urban_mobility_chunks"

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "Settings":
        cwd = Path.cwd()
        inferred = cwd if (cwd / "pyproject.toml").exists() else Path(__file__).resolve().parents[2]
        root = project_root or inferred
        _load_local_env(root / ".env")
        return cls(
            project_root=root,
            knowledge_dir=Path(os.getenv("KNOWLEDGE_DIR", root / "data" / "knowledge_base")),
            index_dir=Path(os.getenv("INDEX_DIR", root / "data" / "index")),
            feedback_path=Path(os.getenv("FEEDBACK_PATH", root / "data" / "feedback" / "feedback.jsonl")),
            embedding_backend=os.getenv("EMBEDDING_BACKEND", "model2vec"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "minishlab/potion-multilingual-128M"),
            model_cache_dir=Path(os.getenv("MODEL_CACHE_DIR", root / "data" / "models")),
            vector_backend=os.getenv("VECTOR_BACKEND", "local"),
            embedding_dimensions=int(os.getenv("EMBEDDING_DIMENSIONS", "384")),
            chunk_size=int(os.getenv("CHUNK_SIZE", "520")),
            chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "80")),
            dense_weight=float(os.getenv("DENSE_WEIGHT", "0.55")),
            sparse_weight=float(os.getenv("SPARSE_WEIGHT", "0.45")),
            retrieve_k=int(os.getenv("RETRIEVE_K", "8")),
            final_k=int(os.getenv("FINAL_K", "4")),
            min_evidence_score=float(os.getenv("MIN_EVIDENCE_SCORE", "0.30")),
            llm_base_url=os.getenv("LLM_BASE_URL", ""),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", "qwen-plus"),
            amap_api_key=os.getenv("AMAP_API_KEY", ""),
            milvus_uri=os.getenv("MILVUS_URI", ""),
            milvus_token=os.getenv("MILVUS_TOKEN", ""),
            milvus_collection=os.getenv("MILVUS_COLLECTION", "urban_mobility_chunks"),
        )
