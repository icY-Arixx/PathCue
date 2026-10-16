"""Deterministic OFFLINE fixture, never registered by the production app."""

from urban_mobility_rag.amap import normalize_route


class FakeAmap:
    data_kind = "demo"

    def __init__(self):
        self.calls = 0
        self.fail = False
        self.empty = False

    def begin_task(self, seconds=40):
        self.calls = 0

    def place_candidates(self, query, city):
        if self.fail:
            raise TimeoutError("fixture timeout")
        origin = "站" in query
        names = [query + "（东侧）", query + "（西侧）"] if origin else [query + "（南门）"]
        return [
            dict(
                id=("o" if origin else "d") + str(i),
                name=n,
                location="121.473700,31.230400" if origin else "121.475100,31.227700",
                city=city,
                address="离线测试位置，不用于真实导航",
                source="offline fixture",
            )
            for i, n in enumerate(names)
        ]

    def plan_routes(self, origin, destination, mode, req):
        self.calls += 1
        if self.fail:
            raise TimeoutError("fixture timeout")
        if self.empty:
            return []
        if mode == "transit":
            paths = [
                dict(
                    duration="1800",
                    distance="6200",
                    walking_distance="600",
                    cost="4",
                    segments=[
                        dict(
                            walking=dict(
                                distance="600", steps=[dict(instruction="步行至测试站", polyline="121.4737,31.2304;121.4740,31.2290")]
                            ),
                            bus=dict(
                                buslines=[
                                    dict(
                                        name="测试2号线",
                                        polyline="121.4740,31.2290;121.4751,31.2277",
                                        departure_stop=dict(name="测试站 A"),
                                        arrival_stop=dict(name="测试站 B"),
                                    )
                                ]
                            ),
                        )
                    ],
                ),
                dict(
                    duration="2400",
                    distance="6600",
                    walking_distance="200",
                    cost="6",
                    segments=[
                        dict(
                            walking=dict(distance="200", steps=[]),
                            bus=dict(buslines=[dict(name="测试公交", polyline="121.4737,31.2304;121.4720,31.2280;121.4751,31.2277")]),
                        )
                    ],
                ),
            ]
        elif mode == "walking":
            paths = [
                dict(
                    duration="3600",
                    distance="4000",
                    steps=[dict(instruction="沿测试道路步行（固定数据）", polyline="121.4737,31.2304;121.4760,31.2290;121.4751,31.2277")],
                )
            ]
        else:
            paths = [dict(distance="7000", cost=dict(duration="1000", tolls="0"), steps=[])]
        routes = [normalize_route(p, mode, i) for i, p in enumerate(paths)]
        for r in routes:
            r.update(source="OFFLINE FIXTURE", data_kind="demo")
        return routes

    def estimate_parking_access(self, destination):
        self.calls += 2
        if self.fail:
            raise TimeoutError("fixture timeout")
        return {
            "found": True,
            "parking_name": "测试停车场",
            "parking_location": "121.475000,31.228000",
            "walk_m": 180,
            "walk_duration_s": 150,
            "source": "offline fixture",
        }
