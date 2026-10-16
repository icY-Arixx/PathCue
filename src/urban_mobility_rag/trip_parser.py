"""Conservative offline updates plus optional bounded JSON extraction."""

import json
import re
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def parse_message(message, settings, use_llm=True):
    if not message.strip():
        return {}, []
    patch, notes = {}, []
    if use_llm and settings.llm_api_key and settings.llm_base_url:
        prompt = (
            "将本轮出行请求解析成 JSON 对象，只输出本轮明确修改的字段。不要补全或猜测地点、日期及条件。"
            "字段 origin,destination,city,date(YYYY-MM-DD),depart_at,arrive_by(HH:MM),budget(元),"
            "max_walk_m(总米数),max_transfers,modes(transit/walking/driving数组),"
            "preferences(fastest/less_walking/fewer_transfers/cheapest数组)。取消限制用null。"
            "预算从30改50只输出budget:50。今天是" + datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        )
        payload = json.dumps(
            dict(
                model=settings.llm_model, temperature=0, messages=[dict(role="system", content=prompt), dict(role="user", content=message)]
            )
        ).encode()
        request = urllib.request.Request(
            settings.llm_base_url.rstrip("/") + "/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings.llm_api_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                content = json.loads(response.read())["choices"][0]["message"]["content"]
            patch = json.loads(content.strip().removeprefix("```json").removesuffix("```").strip())
            if not isinstance(patch, dict):
                raise ValueError("Expected object")
        except Exception:
            notes.append("自然语言服务未成功返回有效条件；已尝试有限规则解析，请核对表单。")
            patch = {}
    elif not settings.llm_api_key:
        notes.append("未配置自然语言服务；当前仅支持有限规则，请在表单核对所有条件。")
    # Deterministic explicit amounts override model extraction.
    patterns = {
        "budget": r"预算[^，。；\n]*?(\d+(?:\.\d+)?)",
        "max_walk_m": r"(?:总步行|步行)[^，。；\n]*?(\d+(?:\.\d+)?)(公里|千米|米)?",
        "max_transfers": r"换乘[^，。；\n]*?(\d+)",
    }
    for field, pattern in patterns.items():
        match = re.search(pattern, message)
        if match:
            tail = re.split(r"[，,。；;\n]|预算|(?:总)?步行|换乘", message[match.end() :], maxsplit=1)[0]
            tail = match[0] + tail
            values = re.findall(r"\d+(?:\.\d+)?", tail)
            value = float(values[-1]) if values else float(match[1])
            if field == "max_walk_m" and re.search(r"公里|千米", tail):
                value *= 1000
            patch[field] = int(value) if field == "max_transfers" else value
    for text, field in [("预算", "budget"), ("步行", "max_walk_m"), ("换乘", "max_transfers"), ("到达", "arrive_by")]:
        if re.search(rf"(?:取消|不限)[^，。]{{0,4}}{text}|{text}(?:不限|不限制)", message):
            patch[field] = None
    od = re.search(r"从(.+?)(?:到|去)(.+?)(?=[，。；\n]|$)", message)
    if od and not re.search(r"预算|步行|换乘|^\d", od[1]):
        patch.update(origin=od[1].strip(), destination=od[2].strip())
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    for phrase, offset in [("今天", 0), ("明天", 1), ("后天", 2)]:
        if phrase in message:
            patch["date"] = (today + timedelta(days=offset)).isoformat()
    iso_date = re.search(r"\d{4}-\d{2}-\d{2}", message)
    if iso_date:
        patch["date"] = iso_date[0]
    t = re.search(r"(\d{1,2}[:：]\d{2})\s*(出发|前到|到达)|(?:出发|到达)[^\d]{0,4}(\d{1,2}[:：]\d{2})", message)
    if t:
        value = (t[1] or t[3]).replace("：", ":").zfill(5)
        patch["arrive_by" if "到" in t[0] else "depart_at"] = value
    for phrase, pref in [("少走路", "less_walking"), ("最快", "fastest"), ("少换乘", "fewer_transfers"), ("便宜", "cheapest")]:
        if phrase in message:
            patch["preferences"] = [pref]
    if not patch:
        notes.append("本轮未识别到可更新条件，请用表单修改；若要换一趟行程，请点“新行程”。")
    return patch, notes
