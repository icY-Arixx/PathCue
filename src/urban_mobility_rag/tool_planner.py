"""One model decision, at most three validated tools; no shell or planning loop."""

import json
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, create_model

from .trips import Requirements

PATCH_FIELDS = {k: f for k, f in Requirements.model_fields.items() if k not in {"hard_constraints", "timezone"}}
TripPatch = create_model(
    "TripPatch",
    __config__=ConfigDict(extra="forbid"),
    **{name: (field.annotation, Field(default=None)) for name, field in PATCH_FIELDS.items()},
)


class PlanTrip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patch: TripPatch


class WeatherQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str | None = Field(default=None, min_length=2, max_length=80)
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class KnowledgeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=2, max_length=1000)


class Clarify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=2, max_length=240)


SCHEMAS = {"plan_trip": PlanTrip, "lookup_weather": WeatherQuery, "search_knowledge": KnowledgeQuery, "clarify": Clarify}
DESCRIPTIONS = {
    "plan_trip": "更新明确修改的行程条件并调用真实路线工具。patch只含本轮修改；null取消限额；省略保留，0是零。查询或重查当前路线可传空patch。不得自动放宽硬条件或确认地点。",
    "lookup_weather": "查询指定城市和日期的真实天气。省略city/date沿用当前行程，否则默认今天。不得把天气条件写入路线数字。",
    "search_knowledge": "查询本地校门、站口、通行规定等有来源的资料，保留当前路线与条件。",
    "clarify": "无法理解、超出工具能力或缺少用户意图时提出一个必要问题，不编造事实。",
}


class ToolPlanner:
    def __init__(self, settings):
        self.settings = settings

    @property
    def enabled(self):
        return bool(self.settings.llm_api_key and self.settings.llm_base_url)

    def plan(self, message, state):
        # Only current structured requirements and two user turns, no GPS/address,
        # tool raw payloads, notices, keys, or full historical logs.
        requirements = dict(state["requirements"])
        if (state.get("confirmed", {}).get("origin") or {}).get("source", "").startswith("浏览器定位"):
            requirements["origin"] = "当前位置（坐标不提供给模型）"
        context = dict(requirements=requirements, recent_user_messages=[h["user"] for h in state.get("history", [])[-2:]])
        tools = [
            dict(type="function", function=dict(name=name, description=DESCRIPTIONS[name], parameters=schema.model_json_schema()))
            for name, schema in SCHEMAS.items()
        ]
        system = (
            "你是PathCue出行助手，只通过给定函数处理任务。一次最多3个不同工具，clarify不能与其他工具同时使用。"
            "行程条件是当前已知状态。只修改本轮明确提出的字段，其他保持不变；不得猜测地点、日期和金额。"
            "新路线和条件修改用plan_trip；校门公告问题用search_knowledge；天气用lookup_weather，可与规划共用。"
            "不支持自动购票、操作设备、设置提醒、无障碍设施核验、顺路途经点拼接。超出范围应clarify。"
            "不能替用户确认地点，不能忽略硬条件。当前上海日期为" + datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        )
        payload = dict(
            model=self.settings.llm_model,
            temperature=0.1,
            max_tokens=1600,
            stream=False,
            messages=[
                dict(role="system", content=system),
                dict(role="user", content="当前状态(JSON数据)：" + json.dumps(context, ensure_ascii=False)),
                dict(role="user", content=message),
            ],
            tools=tools,
            tool_choice="auto",
        )
        if self.settings.llm_model.lower().startswith("glm-"):
            payload["thinking"] = {"type": "disabled"}
        request = urllib.request.Request(
            self.settings.llm_base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.settings.llm_api_key},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(128000)
        calls = json.loads(raw)["choices"][0]["message"].get("tool_calls") or []
        if not 1 <= len(calls) <= 3:
            raise ValueError("Expected one to three tool calls")
        actions = {}
        for call in calls:
            name = call["function"]["name"]
            if name not in SCHEMAS or name in actions or call.get("type", "function") != "function":
                raise ValueError("Unsupported or repeated tool")
            arguments = call["function"]["arguments"]
            if not isinstance(arguments, str) or len(arguments) > 12000:
                raise ValueError("Invalid arguments")
            args = SCHEMAS[name].model_validate_json(arguments).model_dump(mode="json", exclude_unset=True)
            if name == "plan_trip":
                Requirements.model_validate({**state["requirements"], **args["patch"]})
            if name == "lookup_weather" and args.get("date"):
                datetime.strptime(args["date"], "%Y-%m-%d")
            actions[name] = args
        if "clarify" in actions and len(actions) != 1:
            raise ValueError("Conflicting actions")
        return actions
