from __future__ import annotations

import json
import urllib.request

from .models import Citation, SearchHit
from .text import split_sentences, tokenize


class ExtractiveAnswerGenerator:
    def generate(self, question: str, hits: list[SearchHit]) -> tuple[str, list[Citation]]:
        query_tokens = set(tokenize(question))
        candidates: list[tuple[float, str, SearchHit]] = []
        for hit in hits:
            for sentence in split_sentences(hit.chunk.text):
                sentence_tokens = set(tokenize(sentence))
                coverage = len(query_tokens & sentence_tokens) / max(1, len(query_tokens))
                list_boost = 0.0
                if any(marker in question for marker in ("哪些", "什么字段", "需要什么")):
                    if "至少记录" in sentence:
                        list_boost = 0.8
                    elif "包括" in sentence or "字段" in sentence:
                        list_boost = 0.6
                candidates.append((coverage + hit.rerank_score + list_boost, sentence, hit))
        ranked = sorted(candidates, key=lambda item: item[0], reverse=True)
        best_score = ranked[0][0] if ranked else 0.0
        # Do not pad an answer with weakly related evidence merely to reach three citations.
        selected = [item for item in ranked if item[0] >= best_score * 0.72][:3]
        if not selected:
            return "知识库中暂未检索到足够证据，请补充问题或更新资料。", []
        citations: list[Citation] = []
        answer_parts: list[str] = []
        seen_chunks: set[str] = set()
        for _, sentence, hit in selected:
            if hit.chunk.chunk_id in seen_chunks:
                continue
            seen_chunks.add(hit.chunk.chunk_id)
            citation_id = len(citations) + 1
            answer_parts.append(f"{sentence}[{citation_id}]")
            citations.append(Citation(citation_id, hit.chunk.doc_id, hit.chunk.title, hit.chunk.source, hit.chunk.chunk_id, sentence[:160], hit.rerank_score))
        return "".join(answer_parts), citations


class OpenAICompatibleAnswerGenerator:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def generate(self, question: str, hits: list[SearchHit]) -> tuple[str, list[Citation]]:
        citations = [Citation(i, hit.chunk.doc_id, hit.chunk.title, hit.chunk.source, hit.chunk.chunk_id, hit.chunk.text[:160], hit.rerank_score) for i, hit in enumerate(hits, 1)]
        context = "\n\n".join(f"[{item.citation_id}] {hit.chunk.text}" for item, hit in zip(citations, hits))
        prompt = f"仅依据给定资料回答，关键事实后标注[编号]。资料不足时明确说明。\n问题：{question}\n资料：\n{context}"
        payload = json.dumps({"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}/chat/completions", data=payload, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read())
        return data["choices"][0]["message"]["content"], citations
