from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from urban_mobility_rag.amap import AmapClient
from urban_mobility_rag.config import Settings


if __name__ == "__main__":
    settings = Settings.from_env()
    if not settings.amap_api_key:
        raise SystemExit("AMAP_API_KEY is not configured. Create .env or set the environment variable first.")
    client = AmapClient(settings.amap_api_key)
    origin = client.geocode("中国地质大学未来城校区", "武汉")
    destination = client.geocode("武汉站", "武汉")
    if not origin.get("found") or not destination.get("found"):
        raise RuntimeError("Amap geocoding did not return both endpoints")
    route = client.plan_driving(origin["location"], destination["location"])
    pois = client.search_poi("地铁站", "武汉", page_size=3)
    payload = {
        "provider": "Amap Web Service",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "origin_query": "中国地质大学未来城校区",
        "destination_query": "武汉站",
        "origin": origin,
        "destination": destination,
        "route": route,
        "nearby_pois": pois,
    }
    output = Path(__file__).resolve().parents[1] / "artifacts" / "amap_demo.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
