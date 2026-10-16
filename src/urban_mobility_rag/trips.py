"""Public trip contract. Omitted patch fields preserve values; null clears limits."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, time, timedelta
from math import ceil
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Place(BaseModel):
    id: str
    name: str
    location: str
    city: str = ""
    address: str = ""
    source: str = "Amap"
    kind: Literal["railway_station", "metro_station", "other"] = "other"

    @field_validator("location")
    @classmethod
    def coordinates(cls, value):
        lon, lat = map(float, value.split(","))
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("Invalid coordinates")
        return value


class Requirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: str | None = None
    destination: str | None = None
    city: str | None = None
    date: Date | None = None
    timezone: str = "Asia/Shanghai"
    depart_at: time | None = None
    arrive_by: time | None = None
    budget: float | None = Field(default=None, ge=0, le=100000)
    max_walk_m: float | None = Field(default=None, ge=0, le=100000)
    max_transfers: int | None = Field(default=None, ge=0, le=20)
    modes: list[Literal["transit", "walking", "driving"]] = Field(default_factory=lambda: ["transit", "walking"])
    hard_constraints: list[Literal["budget", "max_walk_m", "max_transfers", "arrive_by", "modes"]] = Field(
        default_factory=lambda: ["budget", "max_walk_m", "max_transfers", "arrive_by", "modes"]
    )
    preferences: list[Literal["fastest", "less_walking", "fewer_transfers", "cheapest"]] = Field(default_factory=lambda: ["fastest"])

    @field_validator("origin", "destination", "city")
    @classmethod
    def clean_text(cls, value):
        if value is not None and (not value.strip() or len(value) > 160):
            raise ValueError("地点或城市需为 1–160 字符")
        return value.strip() if value else value

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        ZoneInfo(value)
        return value

    @field_validator("depart_at", "arrive_by")
    @classmethod
    def local_time(cls, value):
        if value and value.tzinfo is not None:
            raise ValueError("时间请用本地 HH:MM，时区由 timezone 单独指定")
        return value

    @model_validator(mode="after")
    def supported(self):
        if self.timezone != "Asia/Shanghai":
            raise ValueError("当前城市地图仅支持 Asia/Shanghai")
        if not self.modes:
            raise ValueError("至少选择一种交通方式")
        return self


class TripRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9-]{36}$")
    trip_id: str | None = None
    message: str = Field(default="", max_length=1000)
    patch: dict = Field(default_factory=dict)
    new_trip: bool = False
    confirm: dict[str, str] = Field(default_factory=dict)
    action: Literal["weather"] | None = None
    location_token: str | None = Field(default=None, min_length=20, max_length=80)


def new_state(session_id: str, trip_id: str) -> dict:
    return dict(
        session_id=session_id,
        trip_id=trip_id,
        revision=0,
        requirements=Requirements().model_dump(mode="json"),
        confirmed={"origin": None, "destination": None},
        pending_fields=[],
        place_candidates={},
        routes=[],
        evidence=[],
        evidence_status="not_checked",
        knowledge_needs=[],
        assumptions=[],
        tool_status=[],
        history=[],
        weather=None,
        weather_priority=None,
        result_type="clarification",
        message="请填写起点、终点和城市。",
    )


def evaluate(route: dict, req: dict) -> list[dict]:
    """Unknown is never coerced to zero or feasible."""
    checks = []
    for field, actual_key in [("budget", "cost_yuan"), ("max_walk_m", "walk_m"), ("max_transfers", "transfers")]:
        limit = req.get(field)
        if limit is None:
            continue
        actual = route.get(actual_key)
        checks.append(
            dict(
                field=field,
                limit=limit,
                actual=actual,
                status="unknown" if actual is None else "satisfied" if actual <= limit else "violated",
                hard=field in req["hard_constraints"],
            )
        )
    mixed_unknown = any(s.get("mode") == "unknown" for s in route.get("segments", []))
    checks.append(
        dict(
            field="modes",
            status="unknown" if mixed_unknown else "satisfied" if route["mode"] in req["modes"] else "violated",
            hard="modes" in req["hard_constraints"],
        )
    )
    if req.get("arrive_by"):
        # Amap has no reliable arrive-by query; even duration-based arrival is only an estimate.
        estimate = None
        next_day = False
        if req.get("depart_at") and route.get("duration_s") is not None:
            departure = datetime.fromisoformat(f"{req['date']}T{req['depart_at']}")
            deadline = datetime.fromisoformat(f"{req['date']}T{req['arrive_by']}")
            if deadline <= departure:
                deadline += timedelta(days=1)
                next_day = True
            estimate = (deadline - departure).total_seconds() >= route["duration_s"]
        checks.append(
            dict(
                field="arrive_by",
                status="violated" if estimate is False else "unknown",
                estimated_on_time=estimate,
                deadline_next_day=next_day,
                hard="arrive_by" in req["hard_constraints"],
                reason=("截止时间按次日计算；" if next_day else "")
                + "仅有预计耗时，地图接口不能保证按时到达。",
            )
        )
    return checks


def rank_routes(routes, req, adverse_weather=False):
    for route in routes:
        route["estimated_arrival"] = None
        if req.get("depart_at") and req.get("date") and route.get("duration_s") is not None:
            departure = datetime.fromisoformat(f"{req['date']}T{req['depart_at']}")
            arrival = departure + timedelta(minutes=ceil(route["duration_s"] / 60))
            route["estimated_arrival"] = {
                "time": arrival.strftime("%H:%M"),
                "day_offset": (arrival.date() - departure.date()).days,
                "basis": "出发时间＋地图预计耗时",
            }
        route["checks"] = evaluate(route, req)
        hard = [c for c in route["checks"] if c["hard"]]
        route["feasibility"] = (
            "violated"
            if any(c["status"] == "violated" for c in hard)
            else ("unknown" if any(c["status"] == "unknown" for c in hard) else "satisfied")
        )
    fields = {"fastest": "duration_s", "less_walking": "walk_m", "fewer_transfers": "transfers", "cheapest": "cost_yuan"}

    def effective_walk(route):
        if route.get("walk_m") is not None:
            return route["walk_m"]
        parking = route.get("parking_access") or {}
        return parking.get("walk_m", float("inf"))

    def key(route):
        transfers = route.get("transfers")
        weather_keys = (effective_walk(route), transfers if transfers is not None else float("inf")) if adverse_weather else ()
        return (
            {"satisfied": 0, "unknown": 1, "violated": 2}[route["feasibility"]],
            *weather_keys,
            *[route.get(fields[p]) if route.get(fields[p]) is not None else float("inf") for p in req["preferences"]],
        )

    return sorted(routes, key=key)
