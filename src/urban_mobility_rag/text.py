from __future__ import annotations

import re

_LATIN = re.compile(r"[A-Za-z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])|\n+")


def normalize_text(text: str) -> str:
    text = text.replace("\u3000", " ").replace("\r\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    """Mixed Chinese/Latin tokenizer without external segmentation packages."""
    normalized = normalize_text(text).lower()
    latin = _LATIN.findall(normalized)
    cjk = "".join(_CJK.findall(normalized))
    chars = list(cjk)
    bigrams = [cjk[i : i + 2] for i in range(max(0, len(cjk) - 1))]
    return latin + chars + bigrams


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT.split(normalize_text(text)) if part.strip()]

