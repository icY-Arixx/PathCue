"""Explicit isolated visual-QA server. Does not load secrets or call real Amap."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn

from tests.fakes import FakeAmap
from urban_mobility_rag.api import create_app
from urban_mobility_rag.config import Settings

if os.getenv("QA_FIXTURE_MODE") != "1":
    raise SystemExit("Set QA_FIXTURE_MODE=1 explicitly. Test data only.")
root = Path(__file__).resolve().parents[1]
settings = Settings(
    root, root / "data/knowledge_base", root / "data/index-qa", root / "data/index-qa/feedback.jsonl", min_evidence_score=0.15
)
app = create_app(settings)


class VisualFake(FakeAmap):
    def plan_routes(self, origin, destination, mode, req):
        time.sleep(0.6)
        if req.get("budget") == 13:
            raise TimeoutError("QA scenario")
        return super().plan_routes(origin, destination, mode, req)


app.state.assistant.client = VisualFake()
uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
