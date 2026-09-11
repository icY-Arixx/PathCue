from __future__ import annotations

import json
from pathlib import Path


GROUPS = {
    "cycling_safety": [
        ("夜间骑行应检查哪些安全装备？", "knowledge"), ("骑行前为什么要检查制动和胎压？", "knowledge"),
        ("雨天骑自行车应该注意什么？", "knowledge"), ("共享单车停放有什么要求？", "knowledge"),
        ("骑行经过路口时如何降低风险？", "knowledge"), ("自行车道被占用时应该怎么办？", "knowledge"),
        ("夜间骑行需要使用车灯吗？", "knowledge"), ("骑行安全规则包含哪些内容？", "knowledge"),
    ],
    "metro_transfer": [
        ("地铁换乘通常需要预留多长时间？", "knowledge"), ("末班车换乘为什么要增加缓冲时间？", "knowledge"),
        ("携带大件行李换乘地铁需要注意什么？", "knowledge"), ("无障碍换乘应优先选择什么设施？", "knowledge"),
        ("换乘距离较长时如何规划时间？", "knowledge"), ("地铁站换乘指引包括哪些信息？", "knowledge"),
        ("如何避免错过地铁末班车？", "knowledge"), ("带儿童换乘应额外预留时间吗？", "knowledge"),
    ],
    "parking_charging": [
        ("充电车位能否停放燃油车？", "knowledge"), ("停车场充电设施信息要记录哪些字段？", "knowledge"),
        ("如何查询附近可用充电桩？", "poi_search"), ("充电站开放时间变化后如何更新？", "knowledge"),
        ("停车场入口坐标为什么需要校验？", "knowledge"), ("充电设施故障状态怎样维护？", "knowledge"),
        ("寻找停车场时应关注哪些属性？", "knowledge"), ("停车与充电数据如何避免过期？", "knowledge"),
    ],
    "construction_detour": [
        ("道路施工公告入库需要哪些字段？", "knowledge"), ("施工封路后如何提供绕行路线？", "route_plan"),
        ("道路施工信息怎样进行增量更新？", "knowledge"), ("施工结束后旧公告如何处理？", "knowledge"),
        ("绕行建议为什么要标注影响方向？", "knowledge"), ("道路封闭的起止时间需要怎样校验？", "knowledge"),
        ("施工公告来源单位是否必须记录？", "knowledge"), ("临时交通管制如何进入检索索引？", "knowledge"),
    ],
    "spatial_data_quality": [
        ("POI 数据质量应监控哪些指标？", "knowledge"), ("空间数据中重复点位如何识别？", "knowledge"),
        ("坐标落在城市边界外时应该怎么办？", "knowledge"), ("为什么要统一空间坐标系？", "knowledge"),
        ("空间元数据完整率如何计算？", "knowledge"), ("道路名称与路网 ID 如何关联？", "knowledge"),
        ("多源 POI 融合时如何解决冲突？", "knowledge"), ("空间数据更新及时性怎样评估？", "knowledge"),
    ],
    "traffic_notice_lifecycle": [
        ("交通事件公告的业务主键是什么？", "knowledge"), ("公告内容没有变化时是否重新向量化？", "knowledge"),
        ("交通公告发布前需要哪些审核？", "knowledge"), ("公告撤销后历史版本如何保存？", "knowledge"),
        ("公告坐标应采用什么坐标系？", "knowledge"), ("哪些异常交通公告需要人工复核？", "knowledge"),
        ("交通公告质量告警阈值是什么？", "knowledge"), ("如何记录一次公告字段变更？", "knowledge"),
    ],
    "emergency_accessibility": [
        ("地铁中断多久应评估启动接驳？", "knowledge"), ("应急接驳公告需要包含哪些信息？", "knowledge"),
        ("轮椅乘客应被引导到哪类接驳点？", "knowledge"), ("视障乘客接驳需要提供什么服务？", "knowledge"),
        ("接驳点空间数据需要关联哪些属性？", "knowledge"), ("步行接驳路径能否穿过施工区？", "knowledge"),
        ("应急接驳结束后要统计哪些指标？", "knowledge"), ("接驳信息变化后多久完成更新？", "knowledge"),
    ],
}

NEGATIVES = [
    ("自行车轮胎被扎后怎样补胎？", "knowledge"), ("地铁纪念卡在哪里购买？", "knowledge"),
    ("新能源汽车电池如何自行维修？", "knowledge"), ("如何办理驾驶证换证？", "knowledge"),
    ("今天哪只股票适合买入？", "knowledge"), ("帮我写一份餐厅菜单。", "knowledge"),
    ("武汉明天的精确降雨概率是多少？", "weather"), ("地铁司机招聘需要什么学历？", "knowledge"),
    ("共享单车押金怎么退款？", "knowledge"), ("停车场月卡价格是多少？", "knowledge"),
    ("某品牌充电桩客服电话是多少？", "knowledge"), ("如何预订今晚的酒店房间？", "poi_search"),
]


if __name__ == "__main__":
    rows = []
    sequence = 1
    for doc_id, questions in GROUPS.items():
        for question, intent in questions:
            rows.append({"id": f"p{sequence:03d}", "question": question, "expected_doc_id": doc_id, "expected_intent": intent, "is_answerable": True})
            sequence += 1
    for question, intent in NEGATIVES:
        rows.append({"id": f"n{sequence:03d}", "question": question, "expected_doc_id": None, "expected_intent": intent, "is_answerable": False, "difficulty": "hard_negative"})
        sequence += 1
    target = Path(__file__).resolve().parents[1] / "data" / "evaluation" / "questions.jsonl"
    target.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} rows to {target}")
