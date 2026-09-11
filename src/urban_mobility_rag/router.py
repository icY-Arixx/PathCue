from __future__ import annotations

import re


class IntentRouter:
    def route(self, question: str) -> str:
        if any(keyword in question for keyword in ("天气", "温度", "下雨", "降水", "空气质量")):
            return "weather"
        if re.search(r"从.+到.+", question) or any(keyword in question for keyword in ("怎么去", "怎么走", "路线规划", "导航", "绕行路线", "步行路径")):
            return "route_plan"
        poi_entities = ("POI", "点位", "充电桩", "充电站", "停车场", "接驳点", "地铁站", "医院", "景点", "酒店", "餐厅")
        poi_actions = ("附近", "哪里", "查询", "寻找", "搜索", "预订", "购买")
        if any(entity in question for entity in poi_entities) and any(action in question for action in poi_actions):
            return "poi_search"
        return "knowledge"
