from __future__ import annotations

from typing import Any, Literal, TypedDict


class AgentState(TypedDict, total=False):
    question: str
    intent: str
    result: dict[str, Any]


def build_langgraph(pipeline):
    """Build a real LangGraph when installed; core RAG remains dependency-light."""
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError("Install the 'agent' extra to enable LangGraph") from exc

    def route_node(state: AgentState) -> AgentState:
        return {"intent": pipeline.router.route(state["question"])}

    def knowledge_node(state: AgentState) -> AgentState:
        return {"result": pipeline.query(state["question"]).to_dict()}

    def tool_node(state: AgentState) -> AgentState:
        result = pipeline.query(state["question"]).to_dict()
        result["tool_status"] = "AMAP_API_KEY 配置后可调用高德地图 MCP 工具"
        return {"result": result}

    def select_path(state: AgentState) -> Literal["knowledge", "map_tool"]:
        return "map_tool" if state["intent"] in {"route_plan", "poi_search", "weather"} else "knowledge"

    builder = StateGraph(AgentState)
    builder.add_node("route", route_node)
    builder.add_node("knowledge", knowledge_node)
    builder.add_node("map_tool", tool_node)
    builder.add_edge(START, "route")
    builder.add_conditional_edges("route", select_path, {"knowledge": "knowledge", "map_tool": "map_tool"})
    builder.add_edge("knowledge", END)
    builder.add_edge("map_tool", END)
    return builder.compile()

