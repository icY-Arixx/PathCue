from __future__ import annotations

import json
import urllib.parse
import urllib.request


class AmapClient:
    BASE_URL = "https://restapi.amap.com"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("AMAP_API_KEY is required")
        self.api_key = api_key

    def _get(self, path: str, **params):
        params["key"] = self.api_key
        url = f"{self.BASE_URL}{path}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = json.loads(response.read())
        if str(payload.get("status", "1")) != "1":
            raise RuntimeError(payload.get("info", "Amap request failed"))
        return payload

    def geocode(self, address: str, city: str = "") -> dict:
        payload = self._get("/v3/geocode/geo", address=address, city=city)
        if not payload.get("geocodes"):
            return {"found": False, "address": address}
        item = payload["geocodes"][0]
        return {"found": True, "formatted_address": item.get("formatted_address"), "location": item.get("location"), "adcode": item.get("adcode")}

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
        return {"found": True, "distance_m": path.get("distance"), "duration_s": path.get("cost", {}).get("duration"), "tolls": path.get("cost", {}).get("tolls"), "steps": path.get("steps", [])}


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

