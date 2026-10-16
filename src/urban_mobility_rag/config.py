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


def _bool_env(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


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
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_api_key: str = ""
    llm_model: str = "glm-4.7-flash"
    amap_api_key: str = ""
    amap_js_key: str = ""
    amap_js_security_code: str = ""
    embedding_revision: str = "v1"
    embedding_api_model: str = "text-embedding-v3"
    milvus_uri: str = ""
    milvus_token: str = ""
    milvus_collection: str = "urban_mobility_chunks"
    online_search_backend: str = "tinyfish"
    tinyfish_api_key: str = ""
    tinyfish_search_url: str = "https://api.search.tinyfish.ai"
    tinyfish_fetch_url: str = "https://api.fetch.tinyfish.ai"
    web_search_account_id: str = ""
    web_search_api_token: str = ""
    web_search_gateway_id: str = "default"
    web_search_provider: str = "ceramic"
    web_search_byok_alias: str = ""
    web_search_timeout_s: int = 10
    web_search_max_results: int = 5
    page_read_timeout_s: int = 8
    page_read_max_bytes: int = 1_000_000
    evidence_cache_ttl_s: int = 86_400
    public_access_mode: bool = False
    public_session_secret: str = ""
    public_cookie_secure: bool = True
    public_requests_per_minute: int = 20

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
            llm_base_url=os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", "glm-4.7-flash"),
            amap_api_key=os.getenv("AMAP_API_KEY", ""),
            amap_js_key=os.getenv("AMAP_JS_KEY", ""),
            amap_js_security_code=os.getenv("AMAP_JS_SECURITY_CODE", ""),
            embedding_revision=os.getenv("EMBEDDING_REVISION", "v1"),
            embedding_api_model=os.getenv("EMBEDDING_API_MODEL", "text-embedding-v3"),
            milvus_uri=os.getenv("MILVUS_URI", ""),
            milvus_token=os.getenv("MILVUS_TOKEN", ""),
            milvus_collection=os.getenv("MILVUS_COLLECTION", "urban_mobility_chunks"),
            online_search_backend=os.getenv("ONLINE_SEARCH_BACKEND", "tinyfish").strip().lower(),
            tinyfish_api_key=os.getenv("TINYFISH_API_KEY", ""),
            tinyfish_search_url=os.getenv("TINYFISH_SEARCH_URL", "https://api.search.tinyfish.ai"),
            tinyfish_fetch_url=os.getenv("TINYFISH_FETCH_URL", "https://api.fetch.tinyfish.ai"),
            web_search_account_id=os.getenv("CLOUDFLARE_ACCOUNT_ID", ""),
            web_search_api_token=os.getenv("CLOUDFLARE_API_TOKEN", ""),
            web_search_gateway_id=os.getenv("CLOUDFLARE_AI_GATEWAY_ID", "default"),
            web_search_provider=os.getenv("WEB_SEARCH_PROVIDER", "ceramic"),
            web_search_byok_alias=os.getenv("WEB_SEARCH_BYOK_ALIAS", ""),
            web_search_timeout_s=int(os.getenv("WEB_SEARCH_TIMEOUT_S", "10")),
            web_search_max_results=int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5")),
            page_read_timeout_s=int(os.getenv("PAGE_READ_TIMEOUT_S", "8")),
            page_read_max_bytes=int(os.getenv("PAGE_READ_MAX_BYTES", "1000000")),
            evidence_cache_ttl_s=int(os.getenv("EVIDENCE_CACHE_TTL_S", "86400")),
            public_access_mode=_bool_env("PUBLIC_ACCESS_MODE"),
            public_session_secret=os.getenv("PUBLIC_SESSION_SECRET", ""),
            public_cookie_secure=_bool_env("PUBLIC_COOKIE_SECURE", True),
            public_requests_per_minute=int(os.getenv("PUBLIC_REQUESTS_PER_MINUTE", "20")),
        )
