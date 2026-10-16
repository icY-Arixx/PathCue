from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from math import ceil

from .models import number


class AmapClient:
    BASE_URL = "https://restapi.amap.com"

    def __init__(self, api_key: str, timeout: float = 8):
        if not api_key:
            raise ValueError("AMAP_API_KEY is required")
        self.api_key = api_key
        self.timeout = timeout
        self.deadline = None
        self.calls = 0

    def begin_task(self, seconds=40):
        self.deadline = time.monotonic() + seconds
        self.calls = 0

    def _get(self, path: str, **params):
        self.calls += 1
        remaining = self.deadline - time.monotonic() if self.deadline else self.timeout
        if self.calls > 8 or remaining <= 0:
            raise TimeoutError("地图工具调用次数或时间预算已用完")
        params["key"] = self.api_key
        url = f"{self.BASE_URL}{path}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=min(self.timeout, remaining)) as response:
            payload = json.loads(response.read())
        if str(payload.get("status", "1")) != "1":
            raise RuntimeError(payload.get("info", "Amap request failed"))
        return payload

    def geocode(self, address: str, city: str = "") -> dict:
        payload = self._get("/v3/geocode/geo", address=address, city=city)
        if not payload.get("geocodes"):
            return {"found": False, "address": address}
        item = payload["geocodes"][0]
        return {
            "found": True,
            "formatted_address": item.get("formatted_address"),
            "location": item.get("location"),
            "adcode": item.get("adcode"),
        }

    def search_poi(self, keywords: str, city: str = "", page_size: int = 5) -> dict:
        payload = self._get("/v5/place/text", keywords=keywords, region=city, page_size=page_size, show_fields="business")
        return {"count": payload.get("count", "0"), "pois": payload.get("pois", [])[:page_size]}

    def plan_driving(self, origin: str, destination: str) -> dict:
        payload = self._get("/v5/direction/driving", origin=origin, destination=destination, show_fields="cost")
        route = payload.get("route", {})
        paths = route.get("paths", [])
        if not paths:
            return {"found": False}
        path = paths[0]
        return {
            "found": True,
            "distance_m": path.get("distance"),
            "duration_s": path.get("cost", {}).get("duration"),
            "tolls": path.get("cost", {}).get("tolls"),
            "steps": path.get("steps", []),
        }

    def place_candidates(self, query: str, city: str):
        pois = self.search_poi(query, city)["pois"]
        candidates = [
            dict(
                id=p["id"],
                name=p["name"],
                location=p["location"],
                city=p.get("cityname") or city,
                address=" ".join(str(p.get(k) or "") for k in ("pname", "cityname", "adname", "address")),
                source="Amap",
                kind="railway_station" if "火车站" in str(p.get("type", "")) else "metro_station" if "地铁站" in str(p.get("type", "")) else "other",
            )
            for p in pois
            if p.get("location") and p.get("id")
        ]
        if not candidates:
            # Keep all geocoding alternatives, never silently select the first homonym.
            payload = self._get("/v3/geocode/geo", address=query, city=city)
            candidates = [
                dict(
                    id=f"geo:{i}:{p['location']}",
                    name=p.get("formatted_address", query),
                    location=p["location"],
                    city=city,
                    address=p.get("formatted_address", ""),
                    source="Amap geocode",
                )
                for i, p in enumerate(payload.get("geocodes", []))
                if p.get("location")
            ]
        return candidates[:5]

    def plan_routes(self, origin, destination, mode, req):
        common = dict(origin=origin["location"], destination=destination["location"])
        if mode == "transit":
            params = dict(
                city=origin.get("city") or req["city"],
                cityd=destination.get("city") or req["city"],
                date=req["date"],
                extensions="all",
                strategy=0,
            )
            if req.get("depart_at"):
                params["time"] = req["depart_at"][:5]
            payload = self._get("/v3/direction/transit/integrated", **common, **params)
            paths = payload.get("route", {}).get("transits", [])
        elif mode == "walking":
            payload = self._get("/v3/direction/walking", **common)
            paths = payload.get("route", {}).get("paths", [])
        else:
            payload = self._get("/v5/direction/driving", **common, show_fields="cost,polyline", strategy=32)
            paths = payload.get("route", {}).get("paths", [])
        return [normalize_route(p, mode, i) for i, p in enumerate(paths[:5])]

    def estimate_parking_access(self, destination: dict) -> dict:
        """Estimate the walk from a nearby parking POI to the destination."""
        payload = self._get(
            "/v5/place/around",
            keywords="停车场",
            location=destination["location"],
            radius=1200,
            sortrule="distance",
            page_size=3,
        )
        parking = next((poi for poi in payload.get("pois", []) if poi.get("location")), None)
        if not parking:
            return {"found": False}
        walking = self._get(
            "/v3/direction/walking",
            origin=parking["location"],
            destination=destination["location"],
        )
        path = next(iter((walking.get("route") or {}).get("paths") or []), None)
        if not path:
            return {"found": False}
        return {
            "found": True,
            "parking_name": parking.get("name") or "终点附近停车场",
            "parking_location": parking["location"],
            "walk_m": number(path.get("distance")),
            "walk_duration_s": number(path.get("duration")),
            "source": "Amap nearby parking + walking route",
        }


def normalize_route(path, mode, index):
    segments = []
    rides = 0
    walk_known = True
    computed_walk = 0
    if mode == "transit":
        for part in path.get("segments", []):
            walk = part.get("walking") or {}
            dist = number(walk.get("distance"))
            if dist is None:
                walk_known = False
            else:
                computed_walk += dist
            for step in walk.get("steps", []):
                segments.append(
                    dict(
                        mode="walking",
                        instruction=step.get("instruction", "步行"),
                        polyline=step.get("polyline", ""),
                        distance_m=number(step.get("distance")),
                    )
                )
            lines = (part.get("bus") or {}).get("buslines", [])
            if lines:
                # buslines are alternative lines for this leg, not consecutive rides.
                line = lines[0]
                rides += 1
                segments.append(
                    dict(
                        mode="transit",
                        instruction=line.get("name", "公共交通"),
                        departure=(line.get("departure_stop") or {}).get("name"),
                        arrival=(line.get("arrival_stop") or {}).get("name"),
                        polyline=line.get("polyline", ""),
                        distance_m=number(line.get("distance")),
                    )
                )
            railway = part.get("railway") or {}
            taxi = part.get("taxi") or {}
            has_railway = isinstance(railway, dict) and bool(railway.get("id") or railway.get("name"))
            has_taxi = isinstance(taxi, dict) and ((number(taxi.get("distance")) or 0) > 0 or bool(taxi.get("sname")))
            if has_railway or has_taxi:
                walk_known = False  # unsupported mixed leg cannot be declared fully known
                segments.append(dict(mode="unknown", instruction="包含未完整解析的铁路或出租车路段", polyline=""))
        walking = number(path.get("walking_distance"))
        if walking is None and walk_known:
            walking = computed_walk
        cost = number(path.get("cost"))
        duration = number(path.get("duration"))
        transfers = max(rides - 1, 0) if rides and not any(s["mode"] == "unknown" for s in segments) else None
    else:
        for step in path.get("steps", []):
            segments.append(
                dict(
                    mode=mode,
                    instruction=step.get("instruction", "行进"),
                    polyline=step.get("polyline", ""),
                    distance_m=number(step.get("distance")),
                )
            )
        walking = number(path.get("distance")) if mode == "walking" else None
        cost = 0 if mode == "walking" else None
        duration = number(path.get("duration") if mode == "walking" else (path.get("cost") or {}).get("duration"))
        transfers = 0
    tolls = number((path.get("cost") or {}).get("tolls")) if mode == "driving" else None
    driving_cost = None
    distance_m = number(path.get("distance"))
    if mode == "driving" and distance_m is not None:
        # Configurable-product heuristic for fuel/energy use, not a taxi fare.
        base_low, base_high = distance_m / 1000 * 0.45, distance_m / 1000 * 0.75
        energy_low, energy_high = ceil(base_low), ceil(base_high)
        driving_cost = {
            "low": ceil(base_low + (tolls or 0)),
            "high": ceil(base_high + (tolls or 0)),
            "energy_low": energy_low,
            "energy_high": energy_high,
            "tolls_yuan": tolls,
            "tolls_included": tolls is not None,
            "basis": "默认燃油车百公里6–10升、参考油价7.5元/升",
            "excludes": "停车费、车辆折旧和时间成本",
        }
    route = dict(
        id=f"{mode}-{index}",
        mode=mode,
        duration_s=duration,
        distance_m=distance_m,
        walk_m=walking,
        transfers=transfers,
        cost_yuan=cost,
        tolls_yuan=tolls,
        estimated_driving_cost=driving_cost,
        parking_access=None,
        segments=segments,
        geometry=[s["polyline"] for s in segments if s.get("polyline")],
        source="Amap Web Service",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        data_kind="real",
        assumptions=["路线耗时为地图服务的预计值。"],
    )
    if mode == "driving":
        route["assumptions"].append("油费按默认燃油车和非实时参考油价估算；停车费、车辆折旧和时间成本另行核实。")
    if mode != "transit":
        route["assumptions"].append("该方式未按所选日期和时间预测交通情况。")
    route["missing_fields"] = [k for k in ("duration_s", "distance_m", "walk_m", "transfers", "cost_yuan") if route[k] is None]
    if not route["geometry"]:
        route["missing_fields"].append("geometry")
    return route


def create_mcp_server(api_key: str):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install the 'agent' extra to enable MCP") from exc
    client = AmapClient(api_key)
    mcp = FastMCP("Urban Mobility Amap Tools")

    @mcp.tool()
    def geocode(address: str, city: str = "") -> dict:
        """Convert a Chinese address or place name to coordinates."""
        return client.geocode(address, city)

    @mcp.tool()
    def search_poi(keywords: str, city: str = "", page_size: int = 5) -> dict:
        """Search points of interest such as stations, parking and hospitals."""
        return client.search_poi(keywords, city, page_size)

    @mcp.tool()
    def plan_driving(origin: str, destination: str) -> dict:
        """Plan a driving route between longitude,latitude coordinates."""
        return client.plan_driving(origin, destination)

    return mcp
