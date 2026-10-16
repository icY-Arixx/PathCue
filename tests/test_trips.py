from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeAmap
from urban_mobility_rag.amap import AmapClient, normalize_route
from urban_mobility_rag.api import create_app
from urban_mobility_rag.app_factory import build_runtime
from urban_mobility_rag.assistant import TripAssistant
from urban_mobility_rag.config import Settings
from urban_mobility_rag.models import Chunk
from urban_mobility_rag.retrieval import eligible
from urban_mobility_rag.trips import Requirements, TripRequest, evaluate, rank_routes

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path):
    return Settings(ROOT, ROOT / "data/knowledge_base", tmp_path / "index", tmp_path / "feedback.jsonl", min_evidence_score=0.15)


@pytest.fixture
def assistant(settings):
    pipeline, _, _ = build_runtime(settings)
    return TripAssistant(settings, pipeline, FakeAmap())


def start(a, **updates):
    patch = dict(
        origin="人民广场站",
        destination="上海博物馆",
        city="上海市",
        date="2026-10-16",
        depart_at="09:00",
        budget=30,
        max_walk_m=800,
        max_transfers=1,
    )
    patch.update(updates)
    return a.handle(TripRequest(patch=patch))


def confirm(a, s):
    return a.handle(
        TripRequest(session_id=s["session_id"], trip_id=s["trip_id"], confirm={k: v[0]["id"] for k, v in s["place_candidates"].items()})
    )


def test_homonyms_require_confirmation_and_map_branch_runs(assistant):
    s = start(assistant)
    assert s["result_type"] == "clarification" and len(s["place_candidates"]["origin"]) == 2
    assert assistant.client.calls == 0
    s = confirm(assistant, s)
    assert s["result_type"] == "trip_plan" and assistant.client.calls == 2
    assert len(s["routes"]) == 3 and s["routes"][0]["cost_yuan"] == 4
    assert s["confirmed"]["origin"]["id"] == "o0"
    assert any(t["tool"] == "knowledge" for t in s["tool_status"])


def test_budget_patch_preserves_conditions_and_restore(assistant, settings):
    s = confirm(assistant, start(assistant))
    changed = assistant.handle(TripRequest(session_id=s["session_id"], message="预算从30提高到50，其他不变"))
    req = changed["requirements"]
    assert req["budget"] == 50 and req["max_walk_m"] == 800 and req["origin"] == "人民广场站"
    assert changed["confirmed"] == s["confirmed"]
    fresh = TripAssistant(settings, assistant.pipeline, FakeAmap())
    assert fresh.sessions.get(s["session_id"]) == changed


def test_cancel_zero_new_trip_and_stale_trip(assistant):
    s = confirm(assistant, start(assistant))
    s = assistant.handle(TripRequest(session_id=s["session_id"], message="取消预算限制", patch={"max_transfers": 0}))
    assert s["requirements"]["budget"] is None and s["requirements"]["max_transfers"] == 0
    old = s["trip_id"]
    s = assistant.handle(TripRequest(session_id=s["session_id"], new_trip=True))
    assert s["trip_id"] != old and s["requirements"]["origin"] is None and s["requirements"]["max_walk_m"] is None
    assert s["confirmed"]["origin"] is None and not s["routes"] and not s["previous_routes"]
    with pytest.raises(ValueError):
        assistant.handle(TripRequest(session_id=s["session_id"], trip_id=old, patch={"budget": 10}))


def test_no_solution_violations_not_auto_relaxed(assistant):
    s = confirm(assistant, start(assistant, budget=0, max_walk_m=0))
    assert s["result_type"] == "no_solution" and s["routes"]
    assert all(r["feasibility"] == "violated" for r in s["routes"])
    assert s["requirements"]["budget"] == 0


def test_unknown_cost_tolls_not_taxi_and_deadline():
    r = normalize_route({"distance": "1000", "cost": {"duration": "900", "tolls": "0"}, "steps": []}, "driving", 0)
    req = Requirements(budget=50, max_walk_m=500, arrive_by="10:00", depart_at="09:00", date="2026-10-16").model_dump(mode="json")
    assert r["cost_yuan"] is None and r["tolls_yuan"] == 0 and r["walk_m"] is None
    assert r["estimated_driving_cost"] == {
        "low": 1,
        "high": 1,
        "energy_low": 1,
        "energy_high": 1,
        "tolls_yuan": 0,
        "tolls_included": True,
        "basis": "默认燃油车百公里6–10升、参考油价7.5元/升",
        "excludes": "停车费、车辆折旧和时间成本",
    }
    checks = {c["field"]: c for c in evaluate(r, req)}
    assert checks["budget"]["status"] == "unknown" and checks["arrive_by"]["status"] == "unknown"
    r["duration_s"] = 7200
    assert next(c for c in evaluate(r, req) if c["field"] == "arrive_by")["status"] == "violated"


def test_cross_midnight_deadline_is_next_day_and_not_a_format_error():
    req = Requirements(depart_at="23:53", arrive_by="02:53", date="2026-10-16").model_dump(mode="json")
    route = {
        "mode": "driving",
        "duration_s": 7200,
        "distance_m": 30_000,
        "walk_m": None,
        "transfers": 0,
        "cost_yuan": None,
        "segments": [],
    }
    check = next(c for c in evaluate(route, req) if c["field"] == "arrive_by")
    assert check["deadline_next_day"] is True
    assert check["estimated_on_time"] is True and check["status"] == "unknown"
    assert "次日" in check["reason"]


def test_routes_include_estimated_arrival_and_parking_walk(assistant):
    state = confirm(assistant, start(assistant, modes=["driving"], depart_at="23:53", arrive_by=None))
    route = state["routes"][0]
    assert route["estimated_arrival"] == {"time": "00:10", "day_offset": 1, "basis": "出发时间＋地图预计耗时"}
    assert route["parking_access"]["walk_m"] == 180
    assert any(item["tool"] == "parking_access" and item["status"] == "ok" for item in state["tool_status"])


def test_timeout_old_routes_not_recommended(assistant):
    s = confirm(assistant, start(assistant))
    assistant.client.fail = True
    s = assistant.handle(TripRequest(session_id=s["session_id"], patch={"budget": 50}))
    assert s["result_type"] == "error" and not s["routes"]
    assert all(r["stale"] for r in s["previous_routes"])
    assert any(t["status"] == "timeout" for t in s["tool_status"])


def test_empty_routes(assistant):
    assistant.client.empty = True
    assert confirm(assistant, start(assistant))["result_type"] == "no_solution"


def test_invalid_fields_dont_corrupt_session(assistant):
    s = confirm(assistant, start(assistant))
    s = assistant.handle(TripRequest(session_id=s["session_id"], patch={"budget": -1}))
    assert s["result_type"] == "error" and s["requirements"]["budget"] == 30 and not s["routes"]
    s = assistant.handle(TripRequest(session_id=s["session_id"], patch={"made_up": True}))
    assert s["result_type"] == "error"


def test_filter_before_retrieval_and_exact_citations(assistant):
    result = assistant.pipeline.query(
        "上海博物馆人民广场馆如何入馆，从哪个地铁出口出站？",
        filters={"audience": "passenger", "data_kind": "real", "city": "上海市", "on_date": "2026-10-16", "facilities": ["上海博物馆"]},
    )
    assert result.citations
    chunks = {c.chunk_id: c for c in assistant.pipeline.retriever.chunks}
    for citation in result.citations:
        assert citation.quote in chunks[citation.chunk_id].text
        assert chunks[citation.chunk_id].metadata["data_kind"] == "real"
        assert citation.doc_id == "shanghai_museum_entrance"
    c = Chunk(
        "id",
        "doc",
        "title",
        "text",
        "source",
        0,
        {"data_kind": "real", "audience": "passenger", "city": "上海市", "valid_until": "2026-10-15"},
    )
    assert not eligible(c, {"data_kind": "real", "audience": "passenger", "city": "上海市", "on_date": "2026-10-16"})
    c.metadata["valid_until"] = "2026-10-16"
    assert eligible(c, {"city": "上海市", "on_date": "2026-10-16"})
    assert not eligible(c, {"city": "北京市"})
    c.metadata["audience"] = "maintainer"
    assert not eligible(c, {"audience": "passenger", "city": "上海市"})


def test_web_enters_graph_missing_key_and_no_secret(settings):
    app = create_app(settings)
    client = TestClient(app)
    r = client.post("/api/trips", json={"patch": {"origin": "A", "destination": "B", "city": "上海市", "depart_at": "09:00"}})
    assert r.status_code == 200 and r.json()["result_type"] == "error"
    assert "AMAP_API_KEY" in r.json()["message"]
    sid = r.json()["session_id"]
    assert client.get("/api/sessions/" + sid).json()["requirements"]["origin"] == "A"
    assert client.post("/query", json={"question": "如何出行"}).json()["result_type"] == "knowledge"
    assert "amap_api_key" not in client.get("/api/config").json()
    assert client.post("/api/trips", json={"patch": {"budget": 0}}).json()["result_type"] == "clarification"
    assert client.get("/").status_code == 200


def test_public_preview_rate_limit_and_security_headers(settings):
    public = Settings(
        settings.project_root,
        settings.knowledge_dir,
        settings.index_dir,
        settings.feedback_path,
        public_access_mode=True,
        public_session_secret="x" * 32,
        public_cookie_secure=False,
        public_requests_per_minute=2,
    )
    client = TestClient(create_app(public))
    assert client.post("/api/trips", json={"patch": {"budget": 10}}).status_code == 200
    assert client.post("/api/trips", json={"patch": {"budget": 20}}).status_code == 200
    blocked = client.post("/api/trips", json={"patch": {"budget": 30}})
    assert blocked.status_code == 429 and blocked.headers["retry-after"] == "60"
    response = client.get("/")
    assert response.headers["x-frame-options"] == "DENY"


def test_amap_official_params_geometry_multiple_routes():
    a = AmapClient("secret")
    origin = {"location": "121,31", "city": "上海市"}
    req = {"city": "上海市", "date": "2026-10-16", "depart_at": "09:00:00"}
    with patch.object(
        a, "_get", return_value={"route": {"transits": [{"duration": "0", "cost": "0", "walking_distance": "0"}, {"duration": "60"}]}}
    ) as get:
        routes = a.plan_routes(origin, origin, "transit", req)
        assert len(routes) == 2 and routes[0]["cost_yuan"] == 0 and routes[1]["cost_yuan"] is None
        assert get.call_args.kwargs["date"] == "2026-10-16" and get.call_args.kwargs["time"] == "09:00"
    with patch.object(a, "_get", return_value={"route": {"paths": []}}) as get:
        a.plan_routes(origin, origin, "driving", req)
        assert "polyline" in get.call_args.kwargs["show_fields"]
    a.begin_task(-1)
    with pytest.raises(TimeoutError):
        a.search_poi("test")


def test_hard_before_soft():
    req = Requirements(budget=5, preferences=["fastest"]).model_dump(mode="json")
    routes = [
        dict(id="fast", mode="transit", duration_s=100, cost_yuan=10, walk_m=0, transfers=0),
        dict(id="slow", mode="transit", duration_s=200, cost_yuan=4, walk_m=0, transfers=0),
    ]
    assert rank_routes(routes, req)[0]["id"] == "slow"


def test_adverse_weather_prioritizes_less_walking_then_fewer_transfers():
    req = Requirements(preferences=["fastest"]).model_dump(mode="json")
    routes = [
        dict(id="fast-wet", mode="transit", duration_s=600, cost_yuan=2, walk_m=900, transfers=0),
        dict(id="more-changes", mode="transit", duration_s=750, cost_yuan=3, walk_m=100, transfers=2),
        dict(id="sheltered", mode="transit", duration_s=900, cost_yuan=4, walk_m=100, transfers=0),
    ]
    ranked = rank_routes(routes, req, adverse_weather=True)
    assert [route["id"] for route in ranked] == ["sheltered", "more-changes", "fast-wet"]


def test_sidecar_and_model_revision_rebuild_with_backup(tmp_path):
    from urban_mobility_rag.chunking import MetadataAwareChunker
    from urban_mobility_rag.embeddings import HashEmbeddingProvider
    from urban_mobility_rag.indexer import KnowledgeBaseIndexer
    from urban_mobility_rag.store import LocalVectorStore

    knowledge = tmp_path / "kb"
    knowledge.mkdir()
    doc = knowledge / "demo.md"
    doc.write_text("城市入口资料。", encoding="utf-8")
    side = knowledge / "demo.md.meta.json"
    side.write_text('{"version":"1"}', encoding="utf-8")
    store = LocalVectorStore(tmp_path / "vectors.json")
    embeddings = HashEmbeddingProvider(16)
    indexer = KnowledgeBaseIndexer(knowledge, tmp_path / "manifest.json", store, embeddings, MetadataAwareChunker(80, 10))
    assert indexer.run().changed_documents == 1
    assert indexer.run().unchanged_documents == 1
    side.write_text('{"version":"2"}', encoding="utf-8")
    assert indexer.run().changed_documents == 1
    assert store.all_chunks()[0].metadata["version"] == "2"
    embeddings.revision = "new-version"
    assert indexer.run().changed_documents == 1
    embeddings.model_name = "another-concrete-model"
    assert indexer.run().changed_documents == 1
    assert list((tmp_path / "backups").glob("*/vectors.json"))


def test_natural_new_trip_does_not_inherit(assistant):
    s = confirm(assistant, start(assistant))
    next_state = assistant.handle(TripRequest(session_id=s["session_id"], message="新行程，从虹桥站到人民广场"))
    assert next_state["trip_id"] != s["trip_id"]
    assert next_state["requirements"]["budget"] is None
    assert next_state["requirements"]["max_walk_m"] is None


def test_parser_multiple_limits(settings):
    from urban_mobility_rag.trip_parser import parse_message

    values, _ = parse_message("预算50元步行500米，换乘0次", settings)
    assert values["budget"] == 50 and values["max_walk_m"] == 500 and values["max_transfers"] == 0


def test_unsupported_facility_requirement_not_silently_dropped(assistant):
    s = confirm(assistant, start(assistant))
    s = assistant.handle(TripRequest(session_id=s["session_id"], message="预算50，不走楼梯"))
    assert s["result_type"] == "clarification" and s["pending_fields"] == ["facility_status"] and not s["routes"]


def test_mixed_unparsed_leg_cannot_satisfy_modes():
    req = Requirements().model_dump(mode="json")
    route = {"mode": "transit", "segments": [{"mode": "unknown"}]}
    assert evaluate(route, req)[0]["status"] == "unknown"


def test_unverified_accessibility_request_survives_later_updates(assistant):
    s = confirm(assistant, start(assistant))
    s = assistant.handle(TripRequest(session_id=s["session_id"], message="不走楼梯"))
    assert s["pending_fields"] == ["facility_status"]
    s = assistant.handle(TripRequest(session_id=s["session_id"], patch={"budget": 50}))
    assert s["pending_fields"] == ["facility_status"] and not s["routes"]
    s = assistant.handle(TripRequest(session_id=s["session_id"], message="取消无障碍要求"))
    assert s["result_type"] == "trip_plan" and not s["unverified_requirements"]


def test_server_key_not_public_and_map_requires_confirmed_places(settings):
    from dataclasses import replace

    app = create_app(replace(settings, amap_api_key="server-only-secret"))
    client = TestClient(app)
    config = client.get("/api/config")
    assert "server-only-secret" not in config.text and config.json()["map_renderer"] == "static"
    state = client.post("/api/trips", json={"patch": {"budget": 10}}).json()
    assert client.get("/api/sessions/" + state["session_id"] + "/map").status_code == 422
    assert client.get("/api/sessions/" + state["session_id"] + "/map?width=99999").status_code == 422


def test_public_mode_binds_sessions_to_signed_visitor_cookie(settings):
    from dataclasses import replace

    app = create_app(
        replace(
            settings,
            public_access_mode=True,
            public_session_secret="a-long-random-test-secret-with-32-chars",
            public_cookie_secure=False,
        )
    )
    owner = TestClient(app)
    other = TestClient(app)
    created = owner.post("/api/trips", json={"patch": {"budget": 10}})
    assert created.status_code == 200
    session_id = created.json()["session_id"]
    cookie = owner.cookies.get("pathcue_visitor")
    assert cookie and "HttpOnly" in created.headers["set-cookie"] and "SameSite=lax" in created.headers["set-cookie"]
    assert owner.get("/api/sessions/" + session_id).status_code == 200
    assert other.get("/api/sessions/" + session_id).status_code == 404
    other.cookies.set("pathcue_visitor", cookie[:-1] + ("a" if cookie[-1] != "a" else "b"))
    assert other.get("/api/sessions/" + session_id).status_code == 404


def test_public_mode_requires_strong_session_secret(settings):
    from dataclasses import replace

    with pytest.raises(ValueError, match="32"):
        create_app(replace(settings, public_access_mode=True, public_session_secret="too-short"))


def test_overlapping_versions_are_flagged(tmp_path):
    from urban_mobility_rag.embeddings import HashEmbeddingProvider
    from urban_mobility_rag.models import Chunk
    from urban_mobility_rag.pipeline import RAGPipeline
    from urban_mobility_rag.retrieval import HybridRetriever
    from urban_mobility_rag.store import LocalVectorStore

    embeddings = HashEmbeddingProvider(16)
    store = LocalVectorStore(tmp_path / "v.json")
    chunks = [
        Chunk(
            str(i),
            str(i),
            "校区出口",
            "校区出口请走" + gate + "门。",
            "source",
            0,
            {"facility": "校区", "version": str(i)},
            embeddings.embed_query(gate),
        )
        for i, gate in enumerate(["南", "北"])
    ]
    store.upsert(chunks)
    result = RAGPipeline(HybridRetriever(chunks, store, embeddings), min_evidence_score=0).query("校区出口走哪个门")
    assert "可能冲突" in result.answer
    assert any(t.get("node") == "version_conflict" for t in result.trace)


def test_rag_followup_uses_trip_context_and_keeps_routes(assistant):
    assistant.pipeline.min_evidence_score = 0.30
    s = confirm(assistant, start(assistant, origin="中国地质大学（南望山校区）", destination="汉口站", city="武汉市"))
    before = s["requirements"].copy()
    route_ids = [r["id"] for r in s["routes"]]
    answer = assistant.handle(TripRequest(session_id=s["session_id"], message="夜间从哪个门出校？"))
    assert answer["result_type"] == "knowledge" and answer["evidence"]
    assert answer["requirements"] == before and [r["id"] for r in answer["routes"]] == route_ids
    assert any(e["doc_id"] == "cug_nanwang_east_night" for e in answer["evidence"])
    assert "东二门" in answer["message"] and "东区" in answer["message"]
    assert "safeguard.cug.edu.cn" in answer["evidence"][0]["source"]


def test_time_scoped_notice_and_restored_citations_are_revalidated(assistant):
    assistant.pipeline.min_evidence_score = 0.30
    s = confirm(assistant, start(assistant, origin="中国地质大学（南望山校区）", destination="汉口站", city="武汉市"))
    assert s["evidence"] and all(e["doc_id"] != "cug_nanwang_east_night" for e in s["evidence"])
    notice = next(c for c in assistant.pipeline.retriever.chunks if c.doc_id == "cug_nanwang_east_night")
    common = {"city": "武汉市", "audience": "passenger", "data_kind": "real", "on_date": "2026-10-17", "facilities": ["南望山校区"]}
    assert not eligible(notice, {**common, "local_time": "09:00"})
    assert eligible(notice, {**common, "local_time": "22:00"})
    assert eligible(notice, {**common, "local_time": "06:29"})
    assert not eligible(notice, {**common, "local_time": "06:30"})
    assistant.pipeline.retriever.chunks = []
    restored = assistant.restore(s["session_id"])
    assert not restored["evidence"] and restored["routes"]
    assert any("资料已更新" in n for n in restored["notes"])


@pytest.mark.parametrize(
    ("question", "contexts", "expected_doc"),
    [
        ("行人如何横过道路？", ["walking", "road_travel"], "national_walking_crossing"),
        ("骑自行车怎么横过机动车道？", ["cycling", "road_travel"], "national_cycling_crossing"),
        ("到火车站后巡游出租车在哪里上车？", ["taxi", "road_travel"], "national_taxi_hub_service"),
        ("地铁末班车换乘要注意什么？", ["public_transit"], "national_metro_information"),
    ],
)
def test_national_baseline_covers_multiple_travel_contexts(assistant, question, contexts, expected_doc):
    result = assistant.pipeline.query(
        question,
        filters={"audience": "passenger", "data_kind": "real", "city": "北京市", "contexts": contexts, "facilities": []},
    )
    assert result.citations and result.citations[0].doc_id == expected_doc
    assert all(c.doc_id == expected_doc for c in result.citations)


def test_general_knowledge_question_does_not_pull_unrelated_current_trip_facility(assistant):
    s = confirm(assistant, start(assistant, origin="中国地质大学（南望山校区）", destination="汉口站", city="武汉市"))
    answer = assistant.handle(TripRequest(session_id=s["session_id"], message="骑自行车怎么横过机动车道？"))
    assert answer["routes"] == s["routes"]
    assert answer["evidence"] and {item["doc_id"] for item in answer["evidence"]} == {"national_cycling_crossing"}
    assert "来访车辆" not in answer["message"]


def test_current_road_status_does_not_use_static_national_rule_as_live_evidence(assistant):
    result = assistant.pipeline.query(
        "人民广场附近道路现在临时封路吗？",
        filters={"audience": "passenger", "data_kind": "real", "city": "上海市", "contexts": ["road_travel"], "facilities": []},
    )
    assert not result.citations
    assert any(item.get("node") == "live_evidence_required" for item in result.trace)
