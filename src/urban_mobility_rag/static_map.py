"""Bounded server-side Amap static-map fallback. The Web key never reaches HTML."""

import math
import urllib.parse
import urllib.request
from collections import OrderedDict
from threading import Lock


def coordinates(route):
    points = []
    for line in route.get("geometry", []):
        for pair in line.split(";"):
            try:
                lon, lat = map(float, pair.split(","))
                if -180 <= lon <= 180 and -85 <= lat <= 85:
                    points.append((lon, lat))
            except (ValueError, TypeError):
                continue
    return points


def camera(state, width, height):
    points = [p for r in state["routes"] for p in coordinates(r)]
    points += [tuple(map(float, p["location"].split(","))) for p in state["confirmed"].values() if p]
    if not points:
        raise ValueError("No confirmed places")
    xy = [(lon / 360 + 0.5, 0.5 - math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / (2 * math.pi)) for lon, lat in points]
    xs, ys = zip(*xy)
    dx, dy = max(max(xs) - min(xs), 1e-8), max(max(ys) - min(ys), 1e-8)
    # Static-map zoom uses a 512px world tile. Keep the same camera for all selections.
    zoom = max(1, min(17, math.floor(math.log2(min((width - 90) / (512 * dx), (height - 90) / (512 * dy))))))
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    center = f"{(cx - 0.5) * 360:.6f},{math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * cy)))):.6f}"
    return center, zoom


class StaticMapProxy:
    def __init__(self, api_key):
        self.api_key = api_key
        self.cache = OrderedDict()
        self.lock = Lock()

    def image(self, state, selected, width=1000, height=760):
        key = (state["session_id"], state["revision"], selected, width, height)
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            center, zoom = camera(state, width, height)
            routes = state["routes"]
            chosen = next((r for r in routes if r["id"] == selected), None)
            if selected and chosen is None:
                raise ValueError("Unknown route")
            visible = [r for r in routes if r is not chosen][:2] + ([chosen] if chosen else [])
            paths = []
            for route in visible:
                points = coordinates(route)
                if len(points) < 2:
                    continue
                # Overview only: retain real vertices and endpoints, cap URL length.
                sampled = points[:: max(1, math.ceil(len(points) / 65))]
                if sampled[-1] != points[-1]:
                    sampled.append(points[-1])
                style = "6,0x176b58,1,," if route is chosen else "3,0x8b9e99,0.65,,"
                paths.append(style + ":" + ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in sampled))
            markers = "|".join(
                f"mid,{color},{label}:{p['location']}"
                for p, color, label in [
                    (state["confirmed"].get("origin"), "0x176b58", "A"),
                    (state["confirmed"].get("destination"), "0xb16546", "B"),
                ]
                if p
            )
            params = dict(key=self.api_key, location=center, zoom=zoom, size=f"{width}*{height}", markers=markers)
            if paths:
                params["paths"] = "|".join(paths)
            url = "https://restapi.amap.com/v3/staticmap?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(url, timeout=8) as response:
                content = response.read(5 * 1024 * 1024)
            if not content.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("Amap static map unavailable")
            self.cache[key] = content
            while len(self.cache) > 32:
                self.cache.popitem(last=False)
            return content
