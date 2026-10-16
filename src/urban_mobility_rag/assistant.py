from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from .amap import AmapClient
from .context_tools import (
    LocationTokens,
    lookup_weather,
    revalidate_weather,
    weather_priority,
    weather_unavailable,
)
from .online_evidence import (
    build_evidence_service,
    candidate_to_state,
    infer_knowledge_need,
)
from .retrieval import eligible, metadata_terms, query_contexts
from .sessions import SessionStore
from .tool_planner import ToolPlanner
from .trip_parser import parse_message
from .trips import Place, Requirements, TripRequest, new_state, rank_routes


class TripAssistant:
    def __init__(self, settings, pipeline, client=None):
        self.settings, self.pipeline = settings, pipeline
        self.client = client or (AmapClient(settings.amap_api_key) if settings.amap_api_key else None)
        self.sessions = SessionStore(settings.index_dir / "sessions.sqlite3")
        self.locations = LocationTokens()
        self.planner = ToolPlanner(settings)
        self.online_evidence = build_evidence_service(settings)
        self.graph = self._graph()

    def _graph(self):
        from .graph import build_langgraph

        return build_langgraph(self)

    def restore(self, session_id, owner_id=None):
        with self.sessions.lock_for(session_id):
            state = self.sessions.get(session_id, owner_id)
            if not state:
                return None
            state.setdefault("knowledge_needs", [])
            state.setdefault("evidence_status", "not_checked")
            state.setdefault("assumptions", [])
            weather = revalidate_weather(state.get("weather"))
            if weather != state.get("weather"):
                state["weather"] = weather
                state["revision"] += 1
                self.sessions.save(state, owner_id)
            chunks = {c.chunk_id: c for c in self.pipeline.retriever.chunks}
            filters = state.get("evidence_filters") or self.filters(state)
            previous = state.get("evidence", [])
            current = []
            for evidence in previous:
                if evidence.get("chunk_id", "").startswith("web:"):
                    retrieved = (evidence.get("metadata") or {}).get("retrieved_at")
                    try:
                        age = datetime.now(ZoneInfo("Asia/Shanghai")) - datetime.fromisoformat(retrieved)
                    except (TypeError, ValueError):
                        age = None
                    if age is not None and age.total_seconds() <= self.settings.evidence_cache_ttl_s:
                        current.append(evidence)
                    continue
                chunk = chunks.get(evidence["chunk_id"])
                if chunk and evidence["claim"] in chunk.text and eligible(chunk, filters):
                    current.append(
                        {
                            **evidence,
                            "metadata": chunk.metadata,
                            "title": chunk.title,
                            "source": chunk.metadata.get("source_url") or chunk.source,
                        }
                    )
            if current != previous:
                state["evidence"] = current
                state["revision"] += 1
                state.setdefault("notes", []).append("本地资料已更新或不再适用，已重新核对先前引用。")
                if state["result_type"] == "knowledge" and len(current) != len(previous):
                    state["message"] = "先前引用的资料已变化，请重新提问以核对当前资料。"
                self.sessions.save(state, owner_id)
            return state

    def handle(self, request: TripRequest, owner_id=None):
        # Mutations are serialized per session. Real map clients and budgets are
        # request-local so unrelated visitors do not reset each other's limits.
        lock_key = request.session_id or owner_id or "__new_session__"
        with self.sessions.lock_for(lock_key):
            state = self.sessions.get(request.session_id, owner_id) if request.session_id else None
            if request.session_id and state is None:
                raise KeyError("会话不存在，请创建新会话")
            if state and request.trip_id and request.trip_id != state["trip_id"]:
                raise ValueError("行程已切换，请刷新当前状态")
            if state is None or request.new_trip or re.search(r"新行程|换个行程|换一趟", request.message):
                state = new_state(request.session_id or str(uuid.uuid4()), str(uuid.uuid4()))
            old_routes = state.get("routes", [])
            old_evidence = state.get("evidence", [])
            state.update(
                tool_status=[],
                evidence=[],
                pending_fields=[],
                notes=[],
                stop=False,
                request=request.model_dump(),
                started=time.monotonic(),
                saved_evidence=old_evidence,
                assumptions=[],
                _owner_id=owner_id,
            )
            client = self.client
            if isinstance(client, AmapClient):
                client = AmapClient(client.api_key, client.timeout)
            state["_client"] = client
            if client:
                client.begin_task(40)
            try:
                state = self.graph.invoke(state, {"recursion_limit": 10})
            except ValidationError as exc:
                fields = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
                names = {
                    "budget": "预算",
                    "max_walk_m": "总步行",
                    "max_transfers": "换乘次数",
                    "modes": "交通方式",
                    "depart_at": "出发时间",
                    "arrive_by": "到达时间",
                    "date": "日期",
                }
                field_text = "、".join(names.get(f, f) for f in fields)
                if field_text:
                    error_message = "条件格式无效，请检查：" + field_text
                else:
                    detail = next((str(e.get("msg", "")) for e in exc.errors() if e.get("msg")), "条件组合无效")
                    error_message = "条件组合无效：" + detail.removeprefix("Value error, ")
                state.update(
                    result_type="error",
                    message=error_message,
                    pending_fields=fields,
                    routes=[],
                    stop=True,
                )
            except Exception:
                # Never expose URLs, credentials, provider bodies, or raw exceptions to the user.
                state.update(result_type="error", message="本轮处理失败，请核对条件后重试。", routes=[], stop=True)
                state["tool_status"].append(dict(tool="workflow", status="failed"))
            if state["result_type"] in {"error", "clarification"} and old_routes:
                state["previous_routes"] = [dict(r, stale=True) for r in old_routes]
            else:
                state["previous_routes"] = []
            state["revision"] += 1
            state["data_kind"] = getattr(client, "data_kind", "real")
            state["history"] = (state.get("history", []) + [dict(user=request.message or "修改表单", assistant=state["message"])])[-20:]
            for key in ("request", "stop", "started", "saved_evidence", "actions", "_client", "_owner_id"):
                state.pop(key, None)
            self.sessions.save(state, owner_id)
            return state

    def update(self, state):
        request = state["request"]
        message = request["message"]
        facility_request = any(t in message for t in ("不走楼梯", "不要楼梯", "轮椅出行", "无障碍路线", "必须有电梯"))
        cancel_facility = any(t in message for t in ("取消无障碍要求", "取消电梯要求", "取消楼梯限制"))
        actions = {}
        if message and not request["patch"] and not request["confirm"] and not request.get("action") and self.planner.enabled:
            result = self.call(state, "model_tools", lambda: self.planner.plan(message, state))
            if result:
                actions = result
                state["tool_status"][-1]["selected_tools"] = list(actions)
            else:
                state["notes"].append("模型服务未返回有效工具调用，已退回有限规则；请核对表单。")
        if "clarify" in actions and (facility_request or cancel_facility):
            actions = {}  # Deterministic unsupported-constraint retention takes precedence.
        state["actions"] = actions
        if "clarify" in actions:
            state.update(
                result_type="clarification", message=actions["clarify"]["question"], routes=[], pending_fields=["user_intent"], stop=True
            )
            return state
        weather_words = any(t in message for t in ("天气", "下雨", "带伞", "降雨"))
        rule_patch, rule_notes = parse_message(message, self.settings, use_llm=False)
        condition_change = any(k not in {"date"} for k in rule_patch)
        weather_only = request.get("action") == "weather" or (
            not request["patch"]
            and not request["confirm"]
            and "plan_trip" not in actions
            and not facility_request
            and not cancel_facility
            and ("lookup_weather" in actions or (not actions and weather_words and not condition_change))
        )
        if weather_only and "search_knowledge" not in actions:
            state["evidence"] = state.get("saved_evidence", [])
            weather_args = actions.get("lookup_weather", {})
            if not actions and weather_words:
                weather_args = dict(date=rule_patch.get("date"))
                city_text = re.split(r"今天|明天|后天|天气|下雨|带伞", message)[0].strip(" ，。的")
                city_text = re.sub(r"^(请问|查询|查一下|看看)", "", city_text)
                if city_text and len(city_text) <= 10:
                    weather_args["city"] = city_text
            self.fetch_weather(state, weather_args)
            weather = state["weather"]
            if state.get("routes"):
                self.compare(state)
                state["message"] = self.weather_message(weather) + " " + state["message"]
                state["stop"] = True
            else:
                state.update(result_type="knowledge", message=self.weather_message(weather), stop=True)
            return state
        knowledge_topic = any(t in message for t in ("哪个门", "哪个出口", "注意什么", "公告", "通行规定", "出校规定"))
        # Knowledge questions remain available without any map key.
        is_knowledge = (
            message
            and not request["patch"]
            and not request["confirm"]
            and ("search_knowledge" in actions or self.pipeline.router.route(message) == "knowledge")
            and "plan_trip" not in actions
            and not facility_request
            and not cancel_facility
            and (
                "search_knowledge" in actions
                or knowledge_topic
                or not any(w in message for w in ("预算", "步行", "换乘", "出发", "到达", "取消", "少走", "最快", "便宜"))
            )
            and not re.search(r"(?:预算|步行|换乘)[^，。]*\d", message)
        )
        if is_knowledge:
            filters = self.filters(state)
            filters["contexts"] = list(set(filters["contexts"] + query_contexts(message)))
            req_places = [state["requirements"].get(k) or "" for k in ("origin", "destination")]
            use_trip_context = any(place and place in message for place in req_places) or any(
                term in message for term in ("哪个门", "出校", "入校", "进站", "到站", "这个站", "这条路线", "起点", "终点", "目的地")
            )
            if use_trip_context:
                filters["facilities"].append(message)
            else:
                filters["city"] = None
                filters["facilities"] = []
            night_question = any(t in message for t in ("夜间", "晚上", "夜里"))
            if night_question:
                filters["local_time"] = "22:00"
            if not filters["city"]:
                matches = [
                    c.metadata.get("city")
                    for c in self.pipeline.retriever.chunks
                    if any(name in message for name in [c.metadata.get("facility"), *metadata_terms(c.metadata.get("facility_aliases"))] if name)
                ]
                if len(set(matches)) == 1:
                    filters["city"] = matches[0]
            context = "；".join(req_places) if use_trip_context else ""
            question = actions.get("search_knowledge", {}).get("question") or message
            state["knowledge_needs"] = [asdict(infer_knowledge_need(question, state["requirements"]))]
            result = self.pipeline.query(question + ("；当前行程：" + context if context.strip("；") else ""), filters=filters)
            state["evidence_filters"] = filters
            state["tool_status"].append(dict(tool="knowledge", status="ok", elapsed_ms=result.latency_ms, trace=result.trace))
            local_evidence = self.evidence(result)
            state.update(result_type="knowledge", message=result.answer, evidence=local_evidence, stop=True)
            if local_evidence:
                state["evidence_status"] = "local_sufficient"
            elif self.online_evidence.enabled:
                online = self.call(state, "online_evidence", lambda: self.online_evidence.find(question, state["requirements"]))
                if online:
                    state["knowledge_needs"] = [asdict(online.need)]
                    state["evidence_status"] = online.status
                    state["evidence"] = [candidate_to_state(item, i) for i, item in enumerate(online.candidates, 1)]
                    state["message"] = online.message
                    if state["evidence"]:
                        state["message"] += " " + "".join(f"{item['claim']}[{item['citation_id']}]" for item in state["evidence"][:2])
                else:
                    state["evidence_status"] = "search_failed"
                    state["message"] = "联网证据查询失败；没有把搜索摘要或旧资料当作当前通行事实。"
            else:
                state["evidence_status"] = "online_not_configured"
                state["assumptions"].append("关键本地规则未核实；当前暂时无法联网补充证据。")
                state["message"] = "现有资料没有足够证据，当前暂时无法联网核实；该通行条件仍待确认。"
            if night_question and state["requirements"].get("depart_at"):
                state["message"] += f" 当前行程仍按{state['requirements']['depart_at'][:5]}出发，以上为夜间规则补充，尚未重查夜间路线。"
            if "lookup_weather" in actions:
                self.fetch_weather(state, actions["lookup_weather"])
                state["message"] += " " + self.weather_message(state["weather"])
            return state
        patch, notes = rule_patch, rule_notes if not actions else []
        if "plan_trip" in actions:
            patch = {**actions["plan_trip"]["patch"], **rule_patch}
        patch.update(request["patch"])
        location = None
        if request.get("location_token"):
            try:
                location = self.locations.consume(request["location_token"], state.get("_owner_id"))
            except ValueError:
                state.update(
                    result_type="clarification",
                    message="定位凭据已过期，请重新定位或手填起点。",
                    routes=[],
                    pending_fields=["location"],
                    stop=True,
                )
                return state
            patch.update(origin=location["place"]["name"], city=location["place"]["city"])
        state["notes"] += notes
        if message and not patch and not request["confirm"] and "plan_trip" not in actions and not facility_request and not cancel_facility:
            state.update(
                result_type="clarification",
                message="未能确认本轮要修改的条件，请用表单补充或明确说明。",
                routes=[],
                pending_fields=["unparsed_request"],
                stop=True,
            )
            return state
        if "timezone" in patch and patch["timezone"] is None:
            patch["timezone"] = "Asia/Shanghai"
        requirements = Requirements.model_validate({**state["requirements"], **patch}).model_dump(mode="json")
        if any(requirements[k] != state["requirements"][k] for k in ("city", "date", "origin", "destination")):
            state["weather"] = None
        for field in ("origin", "destination"):
            if requirements[field] != state["requirements"][field] or requirements["city"] != state["requirements"]["city"]:
                state["confirmed"][field] = None
                state["place_candidates"].pop(field, None)
        state["requirements"] = requirements
        if location:
            state["confirmed"]["origin"] = None
            state["place_candidates"]["origin"] = [location["place"]]
            state["notes"].append(f"定位精度约{location['accuracy_m']:g}米，请核对地址后确认起点；定位不代表可通行入口。")
        state["routes"] = []
        if facility_request:
            state["unverified_requirements"] = [message]
        if cancel_facility:
            state["unverified_requirements"] = []
        if state.get("unverified_requirements"):
            state.update(
                result_type="clarification",
                message="当前无法可靠核实整条路线的无障碍或电梯状态。该要求已保留；请核实设施，或明确说“取消无障碍要求”后继续。",
                pending_fields=["facility_status"],
                stop=True,
            )
            return state
        if not requirements["date"]:
            requirements["date"] = datetime.now(ZoneInfo(requirements["timezone"])).date().isoformat()
            state["notes"].append("未指定日期，按上海时区今天查询。")
        state["pending_fields"] = [f for f in ("origin", "destination", "city") if not requirements[f]]
        if not requirements["depart_at"] and not requirements["arrive_by"]:
            state["pending_fields"].append("time")
        if state["pending_fields"]:
            state.update(result_type="clarification", message="请补充起点、终点、城市及出发时间或到达截止时间。", stop=True)
        return state

    def resolve(self, state):
        client = state.get("_client")
        if not client:
            state.update(result_type="error", message="尚未配置地图服务 AMAP_API_KEY，无法查询真实地点与路线。", stop=True)
            state["tool_status"].append(dict(tool="amap", status="not_configured"))
            return state
        for field in ("origin", "destination"):
            if state["confirmed"][field]:
                continue
            candidates = state["place_candidates"].get(field)
            if candidates is None:
                candidates = self.call(
                    state,
                    "place_" + field,
                    lambda: client.place_candidates(state["requirements"][field], state["requirements"]["city"]),
                )
                if candidates is None:
                    state.update(result_type="error", message="地点查询失败或超时，请稍后重试。", stop=True)
                    return state
                candidates = [Place.model_validate(p).model_dump() for p in candidates]
                state["place_candidates"][field] = candidates
            selected = state["request"]["confirm"].get(field)
            match = next((p for p in candidates if p["id"] == selected), None)
            if selected and not match:
                raise ValueError("请从本次地点候选中确认")
            # Explicit confirmation is required even for a single result (entrance vs facility).
            if match:
                state["confirmed"][field] = match
            else:
                state["pending_fields"].append(field)
        if state["pending_fields"]:
            state.update(result_type="clarification", message="请确认起终点的具体位置；没有匹配时请修改地点或城市。", stop=True)
        return state

    def call(self, state, tool, fn):
        start = time.monotonic()
        try:
            value = fn()
            state["tool_status"].append(dict(tool=tool, status="ok", elapsed_ms=round((time.monotonic() - start) * 1000)))
            return value
        except Exception as exc:
            state["tool_status"].append(
                dict(
                    tool=tool,
                    status="timeout" if isinstance(exc, TimeoutError) else "failed",
                    elapsed_ms=round((time.monotonic() - start) * 1000),
                )
            )
            return None

    def filters(self, state):
        req = state["requirements"]
        contexts = []
        if "transit" in req.get("modes", []):
            contexts.append("public_transit")
        if "walking" in req.get("modes", []):
            contexts.extend(("walking", "road_travel"))
        if "driving" in req.get("modes", []):
            contexts.append("road_travel")
        if any(p and p.get("kind") == "railway_station" for p in state["confirmed"].values()):
            contexts.append("railway")
        return dict(
            audience="passenger",
            data_kind="real",
            city=req.get("city"),
            on_date=req.get("date"),
            local_time=req.get("depart_at"),
            contexts=contexts,
            facilities=[p["name"] for p in state["confirmed"].values() if p] + [req[k] for k in ("origin", "destination") if req.get(k)],
        )

    def evidence(self, result):
        metadata = {c.chunk_id: c.metadata for c in self.pipeline.retriever.chunks}
        conflict = any(t.get("node") == "version_conflict" for t in result.trace)
        return [
            dict(
                citation_id=c.citation_id,
                doc_id=c.doc_id,
                claim=c.quote,
                title=c.title,
                source=metadata.get(c.chunk_id, {}).get("source_url") or c.source,
                chunk_id=c.chunk_id,
                metadata=metadata.get(c.chunk_id, {}),
                scope_note=("存在多个适用版本，请核对是否冲突。" if conflict else "")
                + metadata.get(c.chunk_id, {}).get("source_scope", "设施资料补充；尚未核实本方案经过的具体入口或站口。"),
            )
            for c in result.citations
        ]

    def query(self, state):
        req = state["requirements"]
        client = state.get("_client")
        for mode in req["modes"]:
            routes = self.call(
                state,
                "routes_" + mode,
                lambda: client.plan_routes(state["confirmed"]["origin"], state["confirmed"]["destination"], mode, req),
            )
            if routes is not None:
                state["routes"] += routes
        driving_routes = [route for route in state["routes"] if route["mode"] == "driving"]
        if driving_routes and hasattr(client, "estimate_parking_access"):
            parking = self.call(
                state,
                "parking_access",
                lambda: client.estimate_parking_access(state["confirmed"]["destination"]),
            )
            if parking and parking.get("found"):
                for route in driving_routes:
                    route["parking_access"] = parking
        question = (
            state.get("actions", {}).get("search_knowledge", {}).get("question")
            or f"{req['origin']} 到 {req['destination']} 出行入口及换乘注意事项"
        )
        state["evidence_filters"] = self.filters(state)
        result = self.call(state, "knowledge", lambda: self.pipeline.query(question, filters=state["evidence_filters"]))
        if result:
            state["evidence"] = self.evidence(result)
            state["evidence_status"] = "local_sufficient" if state["evidence"] else "not_found"
        message = state.get("request", {}).get("message", "")
        critical_words = (
            "封路",
            "临时管制",
            "施工",
            "积水",
            "限行",
            "停车",
            "上车点",
            "末班",
            "检票",
            "入口",
            "出口",
            "开放",
        )
        needs_online = not state["evidence"] and (
            "search_knowledge" in state.get("actions", {}) or any(word in message for word in critical_words)
        )
        if needs_online:
            need = infer_knowledge_need(message or question, req)
            state["knowledge_needs"] = [asdict(need)]
            if self.online_evidence.enabled:
                online = self.call(state, "online_evidence", lambda: self.online_evidence.find(message or question, req))
                if online:
                    state["evidence_status"] = online.status
                    state["evidence"] = [candidate_to_state(item, i) for i, item in enumerate(online.candidates, 1)]
                    state["assumptions"].append(online.message)
                else:
                    state["evidence_status"] = "search_failed"
            else:
                state["evidence_status"] = "online_not_configured"
                state["assumptions"].append("关键出行条件缺少现有资料，当前暂时无法联网核实。")
        self.fetch_weather(state, state.get("actions", {}).get("lookup_weather", {}))
        return state

    def fetch_weather(self, state, arguments):
        req = state["requirements"]
        city = arguments.get("city") or req.get("city")
        on_date = arguments.get("date") or req.get("date")
        client = state.get("_client")
        if client is None or getattr(client, "data_kind", "real") == "demo":
            state["weather"] = weather_unavailable("not_configured", "天气服务未配置；没有使用演示天气。", city, on_date)
            return
        value = self.call(state, "lookup_weather", lambda: lookup_weather(client, city, on_date))
        state["weather"] = value or weather_unavailable("error", "天气查询失败或超时，请稍后重试。", city, on_date)
        if value and value["status"] != "ok":
            state["tool_status"][-1]["status"] = value["status"]

    @staticmethod
    def weather_message(weather):
        if weather["status"] != "ok":
            return weather["message"]
        description = (
            weather.get("condition") or f"白天{weather.get('day_condition') or '未知'}，夜间{weather.get('night_condition') or '未知'}"
        )
        return f"{weather['city']} {weather['date']}：{description}。{weather['message']}"

    def compare(self, state):
        priority = weather_priority(state.get("weather"), state["requirements"].get("depart_at"))
        state["weather_priority"] = priority
        routes = rank_routes(state["routes"], state["requirements"], adverse_weather=priority["active"])
        state["routes"] = routes
        failed = any(t["status"] != "ok" for t in state["tool_status"] if t["tool"].startswith("routes_"))
        if failed:
            state["notes"].append("部分交通方式查询失败，本次比较不完整。")
        if not routes:
            state.update(
                result_type="error" if failed else "no_solution",
                message="路线服务失败或超时，请重试。" if failed else "地图未返回可用路线，请检查地点、时间或交通方式。",
            )
        elif any(r["feasibility"] == "satisfied" for r in routes):
            message = (
                priority["message"] + " 再按你的其他偏好排序。"
                if priority["active"]
                else "已按硬条件校验，再按偏好排序。请选择方案查看步骤。"
            )
            state.update(result_type="trip_plan", message=message)
        elif any(r["feasibility"] == "unknown" for r in routes):
            unknown_fields = {
                c["field"]
                for route in routes
                for c in route["checks"]
                if c["hard"] and c["status"] == "unknown"
            }
            names = {
                "budget": "预算",
                "max_walk_m": "总步行",
                "max_transfers": "换乘次数",
                "arrive_by": "到达截止",
                "modes": "交通方式",
            }
            pending = "、".join(names.get(field, field) for field in sorted(unknown_fields))
            arrival_checks = [
                c for route in routes for c in route["checks"] if c["hard"] and c["field"] == "arrive_by"
            ]
            estimated_on_time = sum(c.get("estimated_on_time") is True for c in arrival_checks)
            if unknown_fields == {"arrive_by"} and estimated_on_time:
                message = (
                    f"按预计耗时，{estimated_on_time} 个候选可在截止前到达；实时交通无法保证，请预留缓冲时间。"
                )
            else:
                message = f"以下硬条件只能估算或缺少数据：{pending}。方案已按现有数据排序，请展开方案核对。"
            state.update(result_type="trip_plan", message=message)
        else:
            state.update(result_type="no_solution", message="没有满足全部硬条件的方案。备选已标明冲突，请自行选择是否放宽条件。")
        if len(routes) > 1:
            first = routes[0]
            for r in routes[1:]:
                if first.get("walk_m") is not None and r.get("walk_m") is not None and abs(first["walk_m"] - r["walk_m"]) >= 100:
                    r["tradeoff"] = (
                        f"比首个方案{'多' if r['walk_m'] > first['walk_m'] else '少'}步行 {abs(r['walk_m'] - first['walk_m']):g} 米。"
                    )
        return state
