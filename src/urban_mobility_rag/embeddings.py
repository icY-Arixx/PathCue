from __future__ import annotations

import hashlib
import json
import math
import urllib.request
from pathlib import Path
from typing import Protocol

from .text import tokenize


class EmbeddingProvider(Protocol):
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashEmbeddingProvider:
    """Deterministic local embedding for offline demos and CI."""

    def __init__(self, dimensions: int = 384):
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class Model2VecEmbeddingProvider:
    """Local multilingual neural embeddings distilled from BGE-M3."""

    def __init__(self, model_name: str, cache_dir: Path):
        try:
            from model2vec import StaticModel
        except ImportError as exc:
            raise RuntimeError("Model2Vec mode requires: pip install model2vec") from exc
        local_model = cache_dir / model_name.replace("/", "--")
        if local_model.exists():
            self.model = StaticModel.from_pretrained(local_model, force_download=False)
        else:
            self.model = StaticModel.from_pretrained(model_name)
            local_model.parent.mkdir(parents=True, exist_ok=True)
            self.model.save_pretrained(local_model)
        self.model_name = model_name
        digest = hashlib.sha256()
        for path in sorted(local_model.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(local_model).as_posix().encode())
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
        self.revision = digest.hexdigest()
        self.dimensions = int(self.model.dim)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, show_progress_bar=False, use_multiprocessing=False).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class OpenAICompatibleEmbeddingProvider:
    def __init__(self, base_url: str, api_key: str, model: str, dimensions: int = 1536):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read())
        vectors = [item["embedding"] for item in sorted(data["data"], key=lambda item: item["index"])]
        if len(vectors) != len(texts) or any(len(v) != self.dimensions for v in vectors):
            raise ValueError("Embedding API dimensions/count differs from EMBEDDING_DIMENSIONS; use a new index directory with the correct model configuration")
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
