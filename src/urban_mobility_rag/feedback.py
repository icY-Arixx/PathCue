from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class FeedbackStore:
    def __init__(self, path: Path):
        self.path = path

    def add(self, question: str, answer: str, rating: int, comment: str = "") -> None:
        if rating not in {-1, 1}:
            raise ValueError("rating must be -1 or 1")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), "question": question, "answer": answer, "rating": rating, "comment": comment}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

