import json
from dataclasses import replace
from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.fakes import FakeAmap
from tests.test_trips import ROOT, confirm, start
from urban_mobility_rag.api import create_app
from urban_mobility_rag.app_factory import build_runtime
from urban_mobility_rag.assistant import TripAssistant
from urban_mobility_rag.config import Settings
from urban_mobility_rag.context_tools import (
    LocationFix,
    LocationTokens,
    locate_origin,
    lookup_weather,
    weather_priority,
)
from urban_mobility_rag.tool_planner import ToolPlanner
from urban_mobility_rag.trips import TripRequest

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def settings(tmp_path):
    return Settings(ROOT, ROOT / "data/knowledge_base", tmp_path / "index", tmp_path / "feedback.jsonl", min_evidence_score=0.15)


@pytest.fixture
def assistant(settings):
    return TripAssistant(settings, build_runtime(settings)[0], FakeAmap())


class WeatherClient:
    def __init__(self, age=0, rain=True):
        self.requests = []
        self.age = age
        self.rain = rain

    def _get(self, path, **params):
        self.requests.append((path, params))
        now = datetime.now(TZ)
        if "geocode/geo" in path:
            return {"geocodes": [{"adcode": "420100"}]}
        item = dict(
            city="武汉市",
            weather="小雨" if self.rain else "多云",
            temperature="21",
            reporttime=(now - timedelta(hours=self.age)).strftime("%Y-%m-%d %H:%M:%S"),
        )
        item["casts"] = [
            dict(date=(now + timedelta(days=1)).date().isoformat(), dayweather="小雨", nightweather="多云", daytemp="25", nighttemp="19")
        ]
        return {"lives": [item], "forecasts": [item]}


def test_weather_observation_forecast_staleness_and_range():
    today = datetime.now(TZ).date()
    c = WeatherClient()
    observed = lookup_weather(c, "武汉市", today.isoformat())
    assert observed["kind"] == "observation" and observed["rain_expected"] is True
    assert c.requests[-1][1] == {"city": "420100", "extensions": "base"}
    predicted = lookup_weather(c, "武汉市", (today + timedelta(days=1)).isoformat())
    assert predicted["kind"] == "forecast" and predicted["high_c"] == "25"
    assert c.requests[-1][1]["extensions"] == "all"
    stale = lookup_weather(WeatherClient(age=5), "武汉市")
    assert stale["status"] == "stale" and stale["rain_expected"] is None
    before = len(c.requests)
    assert lookup_weather(c, "武汉市", (today + timedelta(days=9)).isoformat())["status"] == "unavailable"
    assert len(c.requests) == before
    assert lookup_weather(c, None)["status"] == "needs_city"


def test_weather_ambiguous_city_and_missing_forecast():
    class Ambiguous(WeatherClient):
        def _get(self, path, **params):
            return {"geocodes": [{"adcode": "420100"}, {"adcode": "310100"}]}

    assert lookup_weather(Ambiguous(), "同名地点")["status"] == "needs_city"
    day = (datetime.now(TZ) + timedelta(days=2)).date().isoformat()
    assert lookup_weather(WeatherClient(), "武汉市", day)["status"] == "unavailable"


def test_weather_priority_uses_departure_period_and_rejects_stale_data():
    forecast = {
        "status": "ok",
        "kind": "forecast",
        "day_condition": "小雨",
        "night_condition": "多云",
    }
    assert weather_priority(forecast, "09:00")["categories"] == ["降雨"]
    assert weather_priority(forecast, "23:00")["active"] is False
    assert weather_priority({"status": "ok", "kind": "observation", "condition": "霾"})["categories"] == ["雾霾"]
    assert weather_priority({"status": "stale", "kind": "observation", "condition": "暴雪"})["active"] is False


def test_location_requires_fresh_fix_converts_and_expires():
    fix = LocationFix(longitude=114.4, latitude=30.5, accuracy_m=35, obtained_at=datetime.now(TZ))

    class Client:
        def _get(self, path, **params):
            if "convert" in path:
                assert params["coordsys"] == "gps"
                return {"locations": "114.406,30.503"}
            assert params["location"] == "114.406,30.503"
            return {"regeocode": {"formatted_address": "测试位置", "addressComponent": {"city": "武汉市"}}}

    place = locate_origin(Client(), fix)
    assert place["location"] == "114.406,30.503" and place["name"] == "当前位置"
    tokens = LocationTokens()
    receipt = tokens.issue(place, fix)
    assert tokens.consume(receipt["token"])["place"] == place
    with pytest.raises(ValueError):
        tokens.consume(receipt["token"])
    receipt = tokens.issue(place, fix)
    with patch("urban_mobility_rag.context_tools.time.monotonic", return_value=10**12):
        with pytest.raises(ValueError):
            tokens.consume(receipt["token"])
    with pytest.raises(ValidationError):
        LocationFix(longitude=114, latitude=30, accuracy_m=10, obtained_at=datetime.now(TZ) - timedelta(minutes=3))
    with pytest.raises(ValidationError):
        LocationFix(longitude=float("nan"), latitude=30, accuracy_m=10, obtained_at=datetime.now(TZ))


def test_located_origin_still_needs_confirmation(assistant):
    s = confirm(assistant, start(assistant))
    fix = LocationFix(longitude=121, latitude=31, accuracy_m=100, obtained_at=datetime.now(TZ))
    p = dict(id="device:test", name="当前位置", location="121.4,31.2", city="上海市", address="测试设备位置", source="浏览器定位 → 高德")
    receipt = assistant.locations.issue(p, fix)
    result = assistant.handle(TripRequest(session_id=s["session_id"], location_token=receipt["token"]))
    assert result["confirmed"]["origin"] is None and result["place_candidates"]["origin"] == [p]
    assert result["requirements"]["budget"] == 30 and "origin" in result["pending_fields"]
    assert result["confirmed"]["destination"] == s["confirmed"]["destination"]


def model_reply(*calls):
    return BytesIO(
        json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                dict(id=str(i), type="function", function=dict(name=name, arguments=json.dumps(args)))
                                for i, (name, args) in enumerate(calls)
                            ]
                        }
                    }
                ]
            }
        ).encode()
    )


def test_model_tools_execute_existing_workflow_and_preserve_constraints(assistant):
    s = confirm(assistant, start(assistant))
    assistant.planner = ToolPlanner(replace(assistant.settings, llm_api_key="test-secret"))
    with patch(
        "urban_mobility_rag.tool_planner.urllib.request.urlopen", return_value=model_reply(("plan_trip", {"patch": {"budget": 50}}))
    ):
        result = assistant.handle(TripRequest(session_id=s["session_id"], message="预算调整到50，别的照旧"))
    assert result["result_type"] == "trip_plan" and result["requirements"]["budget"] == 50
    assert result["requirements"]["max_walk_m"] == 800 and result["confirmed"] == s["confirmed"]
    assert any(t.get("selected_tools") == ["plan_trip"] for t in result["tool_status"])
    assert any(t["tool"] == "routes_transit" for t in result["tool_status"])


def test_model_weather_and_knowledge_keep_routes(assistant):
    s = confirm(assistant, start(assistant))
    assistant.planner = ToolPlanner(replace(assistant.settings, llm_api_key="test-secret"))
    calls = (("search_knowledge", {"question": "上海博物馆从哪个门入馆？"}), ("lookup_weather", {}))
    with patch("urban_mobility_rag.tool_planner.urllib.request.urlopen", return_value=model_reply(*calls)):
        result = assistant.handle(TripRequest(session_id=s["session_id"], message="博物馆入口和天气各有什么提醒？"))
    assert result["result_type"] == "knowledge" and result["routes"] == s["routes"] and result["evidence"]
    assert result["requirements"] == s["requirements"]


@pytest.mark.parametrize(
    "calls",
    [
        (("run_shell", {"command": "arbitrary"}),),
        (("plan_trip", {"patch": {"hard_constraints": []}}),),
        (("plan_trip", {"patch": {"budget": -1}}),),
        (("lookup_weather", {}), ("lookup_weather", {})),
        (("clarify", {"question": "去哪里？"}), ("lookup_weather", {})),
    ],
)
def test_model_rejects_unapproved_or_invalid_tools(settings, calls):
    planner = ToolPlanner(replace(settings, llm_api_key="test-secret"))
    s = dict(requirements={}, confirmed={}, history=[])
    with patch("urban_mobility_rag.tool_planner.urllib.request.urlopen", return_value=model_reply(*calls)):
        with pytest.raises((ValueError, KeyError)):
            planner.plan("测试", s)


def test_model_failure_falls_back_without_corrupting_state(assistant):
    s = confirm(assistant, start(assistant))
    assistant.planner = ToolPlanner(replace(assistant.settings, llm_api_key="test-secret"))
    with patch("urban_mobility_rag.tool_planner.urllib.request.urlopen", side_effect=TimeoutError("secret must not leak")):
        result = assistant.handle(TripRequest(session_id=s["session_id"], message="预算从30提高到50，其他不变"))
    assert result["requirements"]["budget"] == 50 and result["result_type"] == "trip_plan"
    assert "secret must not leak" not in json.dumps(result)
    assert any(t["tool"] == "model_tools" and t["status"] == "timeout" for t in result["tool_status"])


def test_location_api_validates_and_never_returns_key(settings):
    app = create_app(replace(settings, amap_api_key="server-secret"))
    client = TestClient(app)
    assert client.post("/api/location", json={"longitude": 900}).status_code == 422
    with patch(
        "urban_mobility_rag.api.locate_origin",
        return_value=dict(id="device:test", name="当前位置", location="114.4,30.5", city="武汉市", address="测试地址", source="高德"),
    ):
        response = client.post(
            "/api/location", json=dict(longitude=114.4, latitude=30.5, accuracy_m=10, obtained_at=datetime.now(TZ).isoformat())
        )
    assert response.status_code == 200 and response.json()["coordinate_system"] == "GCJ-02"
    assert "server-secret" not in response.text + client.get("/api/config").text


def test_weather_refresh_and_failure_preserve_confirmed_trip(assistant):
    s = confirm(assistant, start(assistant, date=datetime.now(TZ).date().isoformat()))
    assistant.client.data_kind = "real"
    assistant.client._get = WeatherClient()._get
    result = assistant.handle(TripRequest(session_id=s["session_id"], action="weather"))
    assert result["weather"]["status"] == "ok" and result["weather"]["rain_expected"] is True
    assert result["weather_priority"]["active"] is True and result["routes"][0]["walk_m"] == 200
    assert {route["id"] for route in result["routes"]} == {route["id"] for route in s["routes"]}
    assert result["requirements"] == s["requirements"]
    assert result["evidence"] == s["evidence"]
    with patch.object(assistant.client, "_get", side_effect=TimeoutError("provider details")):
        failed = assistant.handle(TripRequest(session_id=s["session_id"], action="weather"))
    assert failed["weather"]["status"] == "error"
    assert {route["id"] for route in failed["routes"]} == {route["id"] for route in s["routes"]}
    assert "provider details" not in json.dumps(failed)


def test_restored_weather_cannot_remain_fresh(assistant):
    s = confirm(assistant, start(assistant))
    s["weather"] = dict(
        status="ok",
        kind="observation",
        report_time=(datetime.now(TZ) - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S"),
        rain_expected=True,
    )
    assistant.sessions.save(s)
    restored = assistant.restore(s["session_id"])
    assert restored["weather"]["status"] == "stale" and restored["weather"]["rain_expected"] is None
    assert restored["routes"] == s["routes"]


def test_model_context_omits_device_coordinates_and_address(assistant):
    s = confirm(assistant, start(assistant))
    s["confirmed"]["origin"].update(source="浏览器定位 → 高德", address="private-device-address", location="123.456789,32.123456")
    planner = ToolPlanner(replace(assistant.settings, llm_api_key="secret-header-only"))
    with patch("urban_mobility_rag.tool_planner.urllib.request.urlopen", return_value=model_reply(("lookup_weather", {}))) as http:
        planner.plan("看看天气", s)
    payload = http.call_args.args[0].data.decode()
    assert "private-device-address" not in payload and "123.456789" not in payload and "secret-header-only" not in payload
    assert '"tools"' in payload and "glm-4.7-flash" in payload


def test_model_clarification_cannot_drop_unsupported_constraint(assistant):
    s = confirm(assistant, start(assistant))
    assistant.planner = ToolPlanner(replace(assistant.settings, llm_api_key="test-secret"))
    with patch(
        "urban_mobility_rag.tool_planner.urllib.request.urlopen",
        return_value=model_reply(("clarify", {"question": "无法保证电梯开放，要继续吗？"})),
    ):
        result = assistant.handle(TripRequest(session_id=s["session_id"], message="必须有电梯，下雨吗"))
    assert result["pending_fields"] == ["facility_status"] and result["unverified_requirements"] and not result["routes"]
    changed = assistant.handle(TripRequest(session_id=s["session_id"], patch={"budget": 50}))
    assert changed["pending_fields"] == ["facility_status"]
