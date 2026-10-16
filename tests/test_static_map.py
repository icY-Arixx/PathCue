from unittest.mock import MagicMock, patch

from urban_mobility_rag.amap import normalize_route
from urban_mobility_rag.static_map import StaticMapProxy, camera


def test_empty_amap_taxi_and_railway_objects_are_not_real_legs():
    path = {
        "duration": "100",
        "cost": "2",
        "walking_distance": "20",
        "segments": [
            {
                "walking": {"distance": "20"},
                "bus": {"buslines": [{"name": "地铁2号线"}]},
                "railway": {"id": [], "name": []},
                "taxi": {"distance": [], "duration": [], "sname": []},
            }
        ],
    }
    route = normalize_route(path, "transit", 0)
    assert route["transfers"] == 0 and all(s["mode"] != "unknown" for s in route["segments"])


def test_static_map_fixed_camera_cache_and_selected_color():
    routes = [{"id": "a", "geometry": ["114.0,30.0;114.1,30.1"]}, {"id": "b", "geometry": ["114.0,30.0;114.2,30.2"]}]
    state = {
        "session_id": "s",
        "revision": 1,
        "routes": routes,
        "confirmed": {"origin": {"location": "114.0,30.0"}, "destination": {"location": "114.2,30.2"}},
    }
    initial = camera(state, 1000, 760)
    proxy = StaticMapProxy("private-test-key")
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b"\x89PNG\r\n\x1a\nfixture"
    with patch("urban_mobility_rag.static_map.urllib.request.urlopen", return_value=response) as get:
        proxy.image(state, "a")
        proxy.image(state, "a")
        assert get.call_count == 1
        first = get.call_args.args[0]
        proxy.image(state, "b")
        second = get.call_args.args[0]
    from urllib.parse import parse_qs, urlparse

    first, second = [parse_qs(urlparse(u).query) for u in (first, second)]
    assert first["location"] == second["location"] and first["zoom"] == second["zoom"]
    assert first["paths"] != second["paths"]
    assert camera(state, 1000, 760) == initial
