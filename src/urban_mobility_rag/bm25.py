from __future__ import annotations

import math
from collections import Counter

from .models import Chunk, SearchHit
from .text import tokenize


class BM25Index:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.tokens = [tokenize(chunk.text) for chunk in chunks]
        self.term_frequencies = [Counter(tokens) for tokens in self.tokens]
        self.avgdl = sum(len(tokens) for tokens in self.tokens) / max(1, len(self.tokens))
        document_frequency: Counter[str] = Counter()
        for tokens in self.tokens:
            document_frequency.update(set(tokens))
        count = len(chunks)
        self.idf = {term: math.log(1 + (count - freq + 0.5) / (freq + 0.5)) for term, freq in document_frequency.items()}

    def search(self, query: str, k: int = 8) -> list[SearchHit]:
        query_tokens = tokenize(query)
        scored: list[SearchHit] = []
        for chunk, frequencies, tokens in zip(self.chunks, self.term_frequencies, self.tokens):
            score = 0.0
            length_norm = 1 - self.b + self.b * len(tokens) / max(self.avgdl, 1.0)
            for term in query_tokens:
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                score += self.idf.get(term, 0.0) * (frequency * (self.k1 + 1)) / (frequency + self.k1 * length_norm)
            if score > 0:
                scored.append(SearchHit(chunk, score, sparse_score=score))
        return sorted(scored, key=lambda hit: hit.score, reverse=True)[:k]

