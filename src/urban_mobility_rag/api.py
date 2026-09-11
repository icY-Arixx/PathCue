from __future__ import annotations

from pathlib import Path

from .app_factory import build_runtime
from .config import Settings
from .feedback import FeedbackStore


def create_app(settings: Settings | None = None):
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import FileResponse
        from pydantic import BaseModel, Field
    except ImportError as exc:
        raise RuntimeError("Install the 'api' extra to run the web service") from exc

    active_settings = settings or Settings.from_env()
    pipeline, _, index_report = build_runtime(active_settings)
    feedback = FeedbackStore(active_settings.feedback_path)
    app = FastAPI(title="城市出行与空间知识 RAG Agent", version="0.1.0")

    class QueryRequest(BaseModel):
        question: str = Field(min_length=2, max_length=500)

    class FeedbackRequest(BaseModel):
        question: str
        answer: str
        rating: int
        comment: str = ""

    @app.get("/health")
    def health():
        return {"status": "ok", "indexed": getattr(index_report, "scanned_documents", 0)}

    @app.post("/query")
    def query(request: QueryRequest):
        return pipeline.query(request.question, active_settings.retrieve_k, active_settings.final_k).to_dict()

    @app.post("/feedback")
    def add_feedback(request: FeedbackRequest):
        try:
            feedback.add(request.question, request.answer, request.rating, request.comment)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"saved": True}

    @app.get("/")
    def index():
        path = active_settings.project_root / "static" / "index.html"
        return FileResponse(path)

    return app

