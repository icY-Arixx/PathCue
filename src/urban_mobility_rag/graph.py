"""Finite trip workflow; map and knowledge execute within the same task."""


def build_langgraph(assistant):
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(dict)
    graph.add_node("understand_update", assistant.update)
    graph.add_node("confirm_places", assistant.resolve)
    graph.add_node("routes_and_knowledge", assistant.query)
    graph.add_node("validate_compare_explain", assistant.compare)
    graph.add_edge(START, "understand_update")
    graph.add_conditional_edges(
        "understand_update", lambda s: "stop" if s.get("stop") else "places", {"stop": END, "places": "confirm_places"}
    )
    graph.add_conditional_edges(
        "confirm_places", lambda s: "stop" if s.get("stop") else "query", {"stop": END, "query": "routes_and_knowledge"}
    )
    graph.add_edge("routes_and_knowledge", "validate_compare_explain")
    graph.add_edge("validate_compare_explain", END)
    return graph.compile()
