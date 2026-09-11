from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class SourceDocument:
    doc_id: str
    title: str
    text: str
    source: str
    source_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    text: str
    source: str
    chunk_index: int
    metadata: dict[str, Any] = field(default_factory=dict)
    vector: list[float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Chunk":
        return cls(**value)


@dataclass(slots=True)
class SearchHit:
    chunk: Chunk
    score: float
    dense_score: float = 0.0
    sparse_score: float = 0.0
    rerank_score: float = 0.0


@dataclass(slots=True)
class Citation:
    citation_id: int
    doc_id: str
    title: str
    source: str
    chunk_id: str
    quote: str
    score: float


@dataclass(slots=True)
class QueryResult:
    question: str
    answer: str
    intent: str
    confidence: float
    citations: list[Citation]
    trace: list[dict[str, Any]]
    latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

