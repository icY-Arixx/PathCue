"""Real, bounded context adapters. Device coordinates never go to the model."""

import secrets
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

SHANGHAI = ZoneInfo("Asia/Shanghai")
ADVERSE_WEATHER = {
    "降雨": ("雨", "雷电", "冰雹"),
    "降雪": ("雪", "雨夹雪", "冻雨"),
    "雾霾": ("雾", "霾"),
    "沙尘": ("沙尘", "扬沙", "浮尘"),
}


class LocationFix(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    accuracy_m: float = Field(ge=0, le=100000)
    obtained_at: datetime

    @model_validator(mode="after")
    def fresh(self):
        if self.obtained_at.tzinfo is None:
            raise ValueError("定位时间必须包含时区")
        age = (datetime.now(SHANGHAI) - self.obtained_at).total_seconds()
        if not -30 <= age <= 120:
            raise ValueError("定位已过期，请重新获取")
        return self


class LocationTokens:
    """Short lived receipts avoid trusting client supplied POI confirmations."""

    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()

    def issue(self, place, fix, owner_id=None):
        with self.lock:
            now = time.monotonic()
            self.items = {k: v for k, v in self.items.items() if v[0] > now}
            if len(self.items) >= 128:
                self.items.pop(next(iter(self.items)))
            token = secrets.token_urlsafe(24)
            receipt = dict(place=place, accuracy_m=fix.accuracy_m, obtained_at=fix.obtained_at.isoformat(), coordinate_system="GCJ-02")
            self.items[token] = (now + 300, receipt, owner_id)
            return dict(token=token, **receipt)

    def consume(self, token, owner_id=None):
        with self.lock:
            record = self.items.pop(token, None)
            if not record or record[0] <= time.monotonic() or record[2] != owner_id:
                raise ValueError("定位凭据已过期，请重新定位")
            return record[1]


def locate_origin(client, fix: LocationFix):
    # Browser Geolocation is WGS84. Amap routes and reverse geocoding use GCJ-02.
    converted = client._get("/v3/assistant/coordinate/convert", locations=f"{fix.longitude:.6f},{fix.latitude:.6f}", coordsys="gps")
    location = converted.get("locations")
    if not location or ";" in location:
        raise ValueError("定位坐标转换失败")
    result = client._get("/v3/geocode/regeo", location=location, extensions="base", radius=100)
    address = result.get("regeocode") or {}
    component = address.get("addressComponent") or {}
    city = component.get("city") or component.get("province")
    if not isinstance(city, str) or not city or not address.get("formatted_address"):
        raise ValueError("无法确认当前位置所属城市，请手填起点")
    return dict(
        id="device:" + location,
        name="当前位置",
        location=location,
        city=city,
        address=address["formatted_address"],
        source="浏览器定位 → 高德坐标转换与逆地理编码",
    )


def weather_unavailable(status, message, city=None, on_date=None):
    return dict(status=status, message=message, city=city, date=on_date, source="高德天气", retrieved_at=datetime.now(SHANGHAI).isoformat())


def revalidate_weather(weather):
    if not weather or weather.get("status") != "ok":
        return weather
    try:
        report = datetime.fromisoformat(weather["report_time"]).replace(tzinfo=SHANGHAI)
        age = (datetime.now(SHANGHAI) - report).total_seconds()
        if -300 <= age <= (10800 if weather.get("kind") == "observation" else 129600):
            return weather
    except (ValueError, TypeError, KeyError):
        pass
    return {**weather, "status": "stale", "rain_expected": None, "message": "先前天气资料已过期或发布时间无法核实，请更新后再参考。"}


def weather_priority(weather, depart_at=None):
    """Return a bounded routing preference from fresh departure-period weather."""
    inactive = {"active": False, "condition": None, "categories": [], "period": None}
    if not weather or weather.get("status") != "ok":
        return inactive
    if weather.get("kind") == "forecast":
        try:
            hour = int(str(depart_at)[:2]) if depart_at else None
        except (TypeError, ValueError):
            hour = None
        if hour is None:
            condition = " / ".join(filter(None, (weather.get("day_condition"), weather.get("night_condition"))))
            period = "当日预报"
        elif 6 <= hour < 18:
            condition, period = weather.get("day_condition"), "白天预报"
        else:
            condition, period = weather.get("night_condition"), "夜间预报"
    else:
        condition, period = weather.get("condition"), "当前实况"
    if not isinstance(condition, str) or not condition.strip():
        return inactive
    categories = [name for name, terms in ADVERSE_WEATHER.items() if any(term in condition for term in terms)]
    if not categories:
        return {**inactive, "condition": condition, "period": period}
    return {
        "active": True,
        "condition": condition,
        "categories": categories,
        "period": period,
        "message": f"{period}包含{condition}，方案在满足硬条件后优先减少步行和换乘。",
    }


def lookup_weather(client, city, on_date=None):
    """City level observations/dated forecasts, never an hourly rain guarantee."""
    today = datetime.now(SHANGHAI).date()
    requested = date.fromisoformat(on_date) if on_date else today
    if not city:
        return weather_unavailable("needs_city", "请先填写城市，再查询天气。", on_date=requested.isoformat())
    if requested < today or requested > today + timedelta(days=3):
        return weather_unavailable("unavailable", "所选日期超出该天气接口可查询范围，不能据此判断是否下雨。", city, requested.isoformat())
    geo = client._get("/v3/geocode/geo", address=city, city=city)
    locations = geo.get("geocodes") or []
    codes = {p.get("adcode") for p in locations if isinstance(p.get("adcode"), str) and p["adcode"].isdigit()}
    if len(codes) != 1:
        return weather_unavailable("needs_city", "天气城市无法唯一确定，请填写完整城市名称。", city, requested.isoformat())
    kind = "observation" if requested == today else "forecast"
    payload = client._get("/v3/weather/weatherInfo", city=codes.pop(), extensions="base" if kind == "observation" else "all")
    items = payload.get("lives" if kind == "observation" else "forecasts") or []
    if not items:
        return weather_unavailable("unavailable", "天气服务未返回资料，请稍后重试。", city, requested.isoformat())
    item = items[0]
    report_time = item.get("reporttime")
    try:
        reported = datetime.fromisoformat(report_time).replace(tzinfo=SHANGHAI)
        age = (datetime.now(SHANGHAI) - reported).total_seconds()
        fresh = -300 <= age <= (3 * 3600 if kind == "observation" else 36 * 3600)
    except (ValueError, TypeError):
        fresh = False
    result = dict(
        status="ok" if fresh else "stale",
        kind=kind,
        city=item.get("city") or city,
        date=requested.isoformat(),
        source="高德天气",
        source_url="https://lbs.amap.com/api/webservice/guide/api/weatherinfo",
        report_time=report_time,
        retrieved_at=datetime.now(SHANGHAI).isoformat(),
        scope_note="城市或区县级资料，不代表整条路线或具体出发时刻的天气；预报存在不确定性。",
    )
    if kind == "observation":
        result.update(condition=item.get("weather"), temperature_c=item.get("temperature"))
    else:
        forecast = next((f for f in item.get("casts", []) if f.get("date") == requested.isoformat()), None)
        if not forecast:
            return weather_unavailable("unavailable", "天气服务没有所选日期的预报。", city, requested.isoformat())
        result.update(
            day_condition=forecast.get("dayweather"),
            night_condition=forecast.get("nightweather"),
            high_c=forecast.get("daytemp"),
            low_c=forecast.get("nighttemp"),
        )
    descriptions = [result.get(k) for k in ("condition", "day_condition", "night_condition")]
    if not any(isinstance(v, str) and v.strip() for v in descriptions):
        return weather_unavailable("unavailable", "天气描述缺失，不能判断降雨。", city, requested.isoformat())
    result["rain_expected"] = any("雨" in v for v in descriptions if isinstance(v, str)) if fresh else None
    result["message"] = (
        "天气资料已过期或发布时间无法核实，请更新后再参考。"
        if not fresh
        else "天气资料含降雨描述，建议备伞；这不代表具体时刻一定下雨。"
        if result["rain_expected"]
        else "天气资料暂未包含降雨描述，仍请留意临近预报。"
    )
    return result
