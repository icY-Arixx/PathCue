import time

from pydantic import BaseModel, Field

from .amap import AmapClient
from .app_factory import build_runtime
from .assistant import TripAssistant
from .config import Settings
from .context_tools import LocationFix, locate_origin
from .feedback import FeedbackStore
from .identity import VisitorIdentity
from .public_guard import PublicRequestGuard
from .static_map import StaticMapProxy
from .trips import TripRequest


class QueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    session_id: str | None = None


class FeedbackRequest(BaseModel):
    question: str
    answer: str
    rating: int
    comment: str = ""


def create_app(settings: Settings | None = None):
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.responses import FileResponse, JSONResponse, Response
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:
        raise RuntimeError("Install the 'api' extra to run the web service") from exc

    active_settings = settings or Settings.from_env()
    pipeline, _, index_report = build_runtime(active_settings)
    feedback = FeedbackStore(active_settings.feedback_path)
    assistant = TripAssistant(active_settings, pipeline)
    static_map = StaticMapProxy(active_settings.amap_api_key)
    identity = VisitorIdentity(
        active_settings.public_access_mode,
        active_settings.public_session_secret,
        active_settings.public_cookie_secure,
    )
    public_guard = PublicRequestGuard(active_settings.public_access_mode, active_settings.public_requests_per_minute)
    app = FastAPI(title="PathCue｜情境感知与知识增强的出行助手", version="0.2.0")
    app.state.assistant = assistant
    app.mount("/static", StaticFiles(directory=active_settings.project_root / "static"), name="static")

    @app.middleware("http")
    async def public_protection(request: Request, call_next):
        if not public_guard.allow(request):
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请一分钟后再试。"},
                headers={"Retry-After": "60"},
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(self)"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/api/config")
    def public_config():
        return {
            "map_enabled": bool(active_settings.amap_api_key),
            "js_map_key": active_settings.amap_js_key,
            "js_security_code": active_settings.amap_js_security_code,
            "natural_language": bool(active_settings.llm_api_key and active_settings.llm_base_url),
            "model_tools": assistant.planner.enabled,
            "weather_enabled": bool(active_settings.amap_api_key),
            "online_evidence_enabled": assistant.online_evidence.enabled,
            "online_evidence_backend": active_settings.online_search_backend,
            "mode": getattr(assistant.client, "data_kind", "real"),
            "map_renderer": "js" if active_settings.amap_js_key else "static" if active_settings.amap_api_key else "none",
        }

    @app.post("/api/location")
    def resolve_location(fix: LocationFix, request: Request, response: Response):
        owner_id = identity.resolve(request, response)
        if not active_settings.amap_api_key:
            raise HTTPException(status_code=503, detail="地图服务尚未配置，请手填起点。")
        try:
            client = AmapClient(active_settings.amap_api_key)
            client.begin_task(18)
            place = locate_origin(client, fix)
            return assistant.locations.issue(place, fix, owner_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="定位地址查询失败，请手填起点或稍后重试。") from exc

    @app.get("/api/sessions/{session_id}/map")
    def map_image(request: Request, response: Response, session_id: str, route_id: str = "", width: int = 1000, height: int = 760):
        owner_id = identity.resolve(request, response)
        if not active_settings.amap_api_key:
            raise HTTPException(status_code=503, detail="地图服务未配置")
        if not (240 <= width <= 1024 and 180 <= height <= 1024):
            raise HTTPException(status_code=422, detail="地图尺寸无效")
        state = assistant.sessions.get(session_id, owner_id)
        if state is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        if state.get("data_kind") == "demo":
            raise HTTPException(status_code=422, detail="演示路线不能混入真实底图")
        try:
            content = static_map.image(state, route_id, width, height)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="请先确认地点与方案") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="底图暂时不可用，请稍后重试") from exc
        return Response(content, media_type="image/png", headers={"Cache-Control": "private, max-age=60"})

    @app.post("/api/trips")
    def update_trip(payload: TripRequest, request: Request, response: Response):
        owner_id = identity.resolve(request, response)
        try:
            return assistant.handle(payload, owner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="会话不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="行程状态无效，请刷新后重试") from exc

    @app.get("/api/sessions/{session_id}")
    def restore_session(request: Request, response: Response, session_id: str):
        state = assistant.restore(session_id, identity.resolve(request, response))
        if state is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return state

    @app.get("/health")
    def health():
        return {"status": "ok", "indexed": getattr(index_report, "scanned_documents", 0)}

    @app.post("/query")
    def query(payload: QueryRequest, request: Request, response: Response):
        # Preserve legacy answer/citations keys while all web requests enter orchestration.
        started = time.monotonic()
        state = assistant.handle(
            TripRequest(message=payload.question, session_id=payload.session_id),
            identity.resolve(request, response),
        )
        citations = [
            {
                "citation_id": c["citation_id"],
                "doc_id": c["doc_id"],
                "title": c["title"],
                "source": c["source"],
                "chunk_id": c["chunk_id"],
                "quote": c["claim"],
                "score": None,
            }
            for c in state["evidence"]
        ]
        return {
            **state,
            "question": payload.question,
            "answer": state["message"],
            "citations": citations,
            "intent": state["result_type"],
            "confidence": None,
            "trace": state["tool_status"],
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
        }

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
