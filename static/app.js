"use strict";
const $ = (id) => document.getElementById(id);
const fieldNames = [
  "origin",
  "destination",
  "city",
  "date",
  "depart_at",
  "arrive_by",
  "budget",
  "max_walk_m",
  "max_transfers",
];
const labels = {
  budget: "预算",
  max_walk_m: "总步行",
  max_transfers: "换乘",
  arrive_by: "到达截止",
  modes: "交通方式",
};
const modeNames = { transit: "公共交通", walking: "步行", driving: "驾车" };
const icons = { transit: "train-front", walking: "footprints", driving: "car" };
let state = null,
  selected = null,
  dirty = false,
  busy = false,
  restoreFailed = false,
  config = {},
  map = null,
  overlays = [],
  mapSignature = "";
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
function icon(name) {
  const n = el("i");
  n.dataset.lucide = name;
  return n;
}
function refreshIcons() {
  if (window.lucide) window.lucide.createIcons();
}
function safeLink(url, text) {
  try {
    const u = new URL(url);
    if (!["https:", "http:"].includes(u.protocol)) return el("span", "", text);
    const a = el("a", "", text);
    a.href = u.href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    return a;
  } catch {
    return el("span", "", text);
  }
}
function markDirty() {
  dirty = true;
  document.body.classList.add("conditions-dirty");
  $("evidence").hidden = true;
  $("weather").hidden = true;
  $("routes").classList.toggle("stale", Boolean(state?.routes?.length));
  if (state?.routes?.length) $("status").textContent = "条件已修改。点击“规划路线”更新，旧方案已失效。";
  drawMap(false);
}
function currentPatch() {
  const patch = {};
  for (const key of fieldNames) {
    let value = $(key).value.trim() || null;
    if (["budget", "max_walk_m", "max_transfers"].includes(key) && value !== null) value = Number(value);
    let old = state?.requirements[key] ?? null;
    if (["depart_at", "arrive_by"].includes(key) && old) old = old.slice(0, 5);
    if (value !== old) patch[key] = value;
  }
  const modes = [...document.querySelectorAll("input[name=mode]:checked")].map((n) => n.value);
  if (JSON.stringify(modes) !== JSON.stringify(state?.requirements.modes)) patch.modes = modes;
  if ($("preference").value !== state?.requirements.preferences?.[0]) patch.preferences = [$("preference").value];
  return patch;
}
function visibleRoutes(value = state) {
  return value?.routes?.length ? value.routes : value?.previous_routes || [];
}
function captureDraft() {
  return {
    values: Object.fromEntries(fieldNames.map((key) => [key, $(key).value])),
    modes: [...document.querySelectorAll("input[name=mode]:checked")].map((n) => n.value),
    preference: $("preference").value,
  };
}
function restoreDraft(draft) {
  for (const [key, value] of Object.entries(draft.values)) $(key).value = value;
  document.querySelectorAll("input[name=mode]").forEach((n) => (n.checked = draft.modes.includes(n.value)));
  $("preference").value = draft.preference;
  updateTimeHint();
}
function updateTimeHint() {
  const depart = $("depart_at").value;
  const arrive = $("arrive_by").value;
  const overnight = Boolean(depart && arrive && arrive <= depart);
  $("time-hint").hidden = !overnight;
  $("time-hint").textContent = overnight ? `到达截止将按次日 ${arrive} 计算。` : "";
}
function setBusy(value) {
  busy = value;
  document.body.classList.toggle("loading", value);
  document
    .querySelectorAll("form button,#new-trip,.candidate-group button,.weather-refresh")
    .forEach((n) => (n.disabled = value));
  $("trip-form")
    .querySelectorAll("input,select")
    .forEach((n) => (n.disabled = value));
  $("message").disabled = value;
  if (value) {
    $("status").className = "status";
    $("status").textContent = "正在处理行程与查询可用资料…";
  }
}
async function send(payload) {
  if (busy) return;
  if (restoreFailed && !payload.new_trip) {
    $("status").textContent = "会话暂时无法恢复，请刷新重试，或选择新行程。";
    return;
  }
  const draft = captureDraft();
  setBusy(true);
  $("evidence").hidden = true;
  $("weather").hidden = true;
  if (state?.routes?.length) {
    dirty = true;
    $("routes").classList.add("stale");
    drawMap(false);
  }
  try {
    const r = await fetch("/api/trips", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state?.session_id, trip_id: state?.trip_id, ...payload }),
    });
    const data = await r.json();
    if (!r.ok) throw Error(typeof data.detail === "string" ? data.detail : "条件格式无效，请核对输入。");
    state = data;
    restoreFailed = false;
    const shownRoutes = visibleRoutes(data);
    selected =
      shownRoutes.some((r) => r.id === selected)
        ? selected
        : (shownRoutes[0]?.id ?? null);
    dirty = data.result_type === "error";
    localStorage.setItem("trip-session", data.session_id);
    history.replaceState(null, "", location.pathname);
    $("message").value = "";
    render();
    if (data.result_type === "error") {
      restoreDraft(draft);
      document.body.classList.add("conditions-dirty");
      $("routes").classList.toggle("stale", Boolean(shownRoutes.length));
    }
  } catch (e) {
    dirty = true;
    $("status").className = "status error";
    $("status").textContent = "更新失败：" + e.message + " 旧方案已失效，请重试。";
    $("routes").classList.toggle("stale", Boolean(state?.routes?.length));
  } finally {
    setBusy(false);
  }
}
function render() {
  document.body.classList.remove("conditions-dirty");
  $("message").placeholder = state.requirements.origin?.includes("南望山")
    ? "修改条件，或问夜间校门规定"
    : "修改条件，或问当地出行须知";
  if (state.data_kind === "demo") $("product-mode").textContent = "离线固定数据演示";
  const req = state.requirements;
  for (const key of fieldNames) {
    $(key).value = req[key] === null ? "" : ["depart_at", "arrive_by"].includes(key) ? req[key].slice(0, 5) : req[key];
  }
  updateTimeHint();
  document.querySelectorAll("input[name=mode]").forEach((n) => (n.checked = req.modes.includes(n.value)));
  $("preference").value = req.preferences[0] || "fastest";
  const compact = Boolean(req.origin && req.destination && req.city && (req.depart_at || req.arrive_by));
  document.body.classList.toggle("has-results", compact);
  $("itinerary").open = !compact;
  $("conditions").open = !compact;
  $("itinerary-summary").textContent =
    `${state.confirmed.origin?.name || req.origin || "起点"} → ${state.confirmed.destination?.name || req.destination || "终点"} · ${req.date || ""} ${req.depart_at?.slice(0, 5) || "到达 " + (req.arrive_by?.slice(0, 5) || "待定")}`;
  $("condition-values").textContent =
    `${req.budget == null ? "预算不限" : "¥" + req.budget} · ${req.max_walk_m == null ? "步行不限" : req.max_walk_m + "米"} · ${req.max_transfers == null ? "换乘不限" : req.max_transfers + "次换乘"}`;
  $("status").className = "status " + state.result_type;
  $("status").textContent = planMessage();
  $("routes").classList.toggle("stale", state.result_type === "error" && Boolean(visibleRoutes().length));
  $("route-count").textContent = visibleRoutes().length ? `${visibleRoutes().length} 个候选` : "";
  $("map-caption").textContent = req.city || "路线与城市";
  renderCandidates();
  renderRoutes();
  renderEvidence();
  renderWeather();
  $("notes").replaceChildren(...(state.notes || []).map((t) => el("p", "", t)));
  $("history-content").replaceChildren(
    ...state.history.map((h) => {
      const p = el("p");
      p.append(el("strong", "", h.user), el("br"), document.createTextNode(h.assistant));
      return p;
    }),
  );
  $("debug").textContent = JSON.stringify(
    {
      session_id: state.session_id,
      trip_id: state.trip_id,
      revision: state.revision,
      pending_fields: state.pending_fields,
      tools: state.tool_status,
    },
    null,
    2,
  );
  drawMap(true);
  refreshIcons();
  document.querySelector(".panel-scroll").scrollTop = 0;
}
function renderWeather() {
  const section = $("weather");
  const weather = state?.weather;
  section.replaceChildren();
  section.hidden = !weather && !state?.requirements.city;
  if (section.hidden) return;
  const heading = el("div", "weather-heading");
  const button = el("button", "weather-refresh", weather ? "更新天气" : "查询天气");
  button.type = "button";
  button.disabled = busy || dirty;
  button.onclick = () => send({ action: "weather" });
  heading.append(el("h3", "", "出行天气"), button);
  section.append(heading);
  if (!weather) {
    section.append(el("p", "micro", "按行程城市和日期查询，不需要精确定位。"));
    return;
  }
  if (!["ok", "stale"].includes(weather.status)) {
    section.append(el("p", "micro", weather.message));
    return;
  }
  const condition =
    weather.condition || `白天${weather.day_condition || "未知"} / 夜间${weather.night_condition || "未知"}`;
  const temperature =
    weather.kind === "observation"
      ? weather.temperature_c == null
        ? ""
        : `${weather.temperature_c}°C`
      : `${weather.low_c ?? "未知"}–${weather.high_c ?? "未知"}°C`;
  section.append(
    el("p", "weather-summary", `${weather.city} · ${weather.date} · ${weather.kind === "forecast" ? "预报" : "实况"}`),
  );
  section.append(el("p", "", `${weather.status === "stale" ? "资料已过期 · " : ""}${condition} ${temperature}`));
  if (weather.rain_expected) section.append(el("p", "micro", "有降雨描述，建议备伞；请留意临近预报。"));
  if (state.weather_priority?.active)
    section.append(el("p", "weather-priority", "天气不佳，路线已优先少步行、少换乘；仍请留意临近预报。"));
  const details = el("details");
  details.append(el("summary", "micro", "发布时间与适用范围"));
  details.append(el("p", "micro", weather.message));
  details.append(
    el("p", "micro", `发布时间：${weather.report_time || "无法核实"} · 获取时间：${weather.retrieved_at}`),
  );
  details.append(el("p", "micro", weather.scope_note));
  details.append(safeLink(weather.source_url, weather.source));
  section.append(details);
}
async function locateOrigin() {
  if (busy) return;
  if (!window.isSecureContext || !navigator.geolocation) {
    $("location-status").textContent = "此浏览器无法定位，请手填起点；部署页面需使用 HTTPS。";
    return;
  }
  setBusy(true);
  $("location-status").textContent = "正在请求设备位置，请在浏览器中选择是否允许…";
  $("status").textContent = "正在获取位置；也可拒绝授权后手填起点。";
  let receipt = null;
  try {
    const position = await new Promise((resolve, reject) =>
      navigator.geolocation.getCurrentPosition(resolve, reject, {
        enableHighAccuracy: true,
        timeout: 10000,
        maximumAge: 0,
      }),
    );
    $("location-status").textContent = "正在查询当前位置的地址…";
    const response = await fetch("/api/location", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        longitude: position.coords.longitude,
        latitude: position.coords.latitude,
        accuracy_m: position.coords.accuracy,
        obtained_at: new Date(position.timestamp).toISOString(),
      }),
    });
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail === "string" ? data.detail : "定位结果无效，请重新获取。");
    receipt = data;
    $("location-status").textContent = `精度约${Math.round(data.accuracy_m)}米，请核对地址并确认起点。`;
  } catch (error) {
    const messages = {
      1: "未获得定位授权，请手填起点。",
      2: "设备暂时无法定位，请手填起点。",
      3: "定位超时，请重试或手填起点。",
    };
    $("location-status").textContent = messages[error.code] || error.message || "定位失败，请手填起点。";
    $("status").textContent = dirty ? "条件已修改，请点击规划路线更新。" : state?.message || "请手填起终点继续规划。";
  } finally {
    setBusy(false);
  }
  if (receipt) await send({ location_token: receipt.token, patch: currentPatch() });
}
$("locate-origin").onclick = locateOrigin;
function renderCandidates() {
  const root = $("candidates");
  root.replaceChildren();
  for (const field of ["origin", "destination"]) {
    if (state.confirmed[field] || !state.place_candidates[field]) continue;
    const group = el("div", "candidate-group");
    group.append(el("h3", "", `确认${field === "origin" ? "起点" : "终点"}`));
    const candidates = state.place_candidates[field];
    if (!candidates.length) group.append(el("p", "micro", "没有找到匹配地点，请修改名称或城市。"));
    for (const place of candidates) {
      const b = el("button");
      b.type = "button";
      b.append(el("span", "", place.name), el("small", "", place.address));
      b.onclick = () => send({ confirm: { [field]: place.id } });
      group.append(b);
    }
    root.append(group);
  }
}
function fmt(v, unit) {
  return v === null || v === undefined
    ? "待确认"
    : `${Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 0 })}${unit}`;
}
function checkText(check, includeName = true) {
  const name = includeName ? `${labels[check.field]}：` : "";
  if (check.field === "arrive_by") {
    if (check.estimated_on_time === true) return `${name}预计可在截止前到达（不保证）`;
    if (check.estimated_on_time === false) return `${name}按预计耗时无法在截止前到达`;
  }
  const status = { satisfied: "满足", violated: "不满足", unknown: "无法由接口确认" }[check.status];
  return `${name}${status}${check.actual != null ? `（${check.actual} / 上限 ${check.limit}）` : ""}`;
}
function drivingCostEstimate(route) {
  if (route.estimated_driving_cost) return route.estimated_driving_cost;
  if (route.mode !== "driving" || route.distance_m == null) return null;
  const tolls = route.tolls_yuan == null ? 0 : route.tolls_yuan;
  const energyLow = Math.ceil((route.distance_m / 1000) * 0.45);
  const energyHigh = Math.ceil((route.distance_m / 1000) * 0.75);
  return {
    low: energyLow + tolls,
    high: energyHigh + tolls,
    energy_low: energyLow,
    energy_high: energyHigh,
    tolls_yuan: route.tolls_yuan,
    tolls_included: route.tolls_yuan != null,
    basis: "默认燃油车百公里6–10升、参考油价7.5元/升",
    excludes: "停车费、车辆折旧和时间成本",
  };
}
function arrivalEstimate(route) {
  if (route.estimated_arrival) return route.estimated_arrival;
  const depart = state?.requirements?.depart_at;
  if (!depart || route.duration_s == null) return null;
  const [hour, minute] = depart.slice(0, 5).split(":").map(Number);
  const totalMinutes = hour * 60 + minute + Math.ceil(route.duration_s / 60);
  return {
    time: `${String(Math.floor(totalMinutes / 60) % 24).padStart(2, "0")}:${String(totalMinutes % 60).padStart(2, "0")}`,
    day_offset: Math.floor(totalMinutes / 1440),
    basis: "出发时间＋地图预计耗时",
  };
}
function planMessage() {
  const routes = visibleRoutes();
  if (state.result_type !== "trip_plan" || !routes.length) return state.message;
  const arrivalChecks = routes.flatMap((r) => r.checks.filter((c) => c.hard && c.field === "arrive_by"));
  const estimatedOnTime = arrivalChecks.filter((c) => c.estimated_on_time === true).length;
  const estimatedLate = arrivalChecks.filter((c) => c.estimated_on_time === false).length;
  const otherUnknown = routes.some((r) => r.checks.some((c) => c.hard && c.status === "unknown" && c.field !== "arrive_by"));
  if (arrivalChecks.length && !otherUnknown) {
    if (estimatedOnTime)
      return `按预计耗时，${estimatedOnTime} 个候选可在截止前到达；实时交通无法保证，请预留缓冲时间。`;
    if (estimatedLate)
      return `按预计耗时，${estimatedLate} 个候选无法在截止前到达；请提前出发或调整截止时间。`;
    return "到达截止只能结合预计耗时判断；当前缺少完整时间数据，请预留缓冲时间。";
  }
  return state.message;
}
function renderRoutes() {
  const root = $("routes");
  root.replaceChildren();
  const routes = visibleRoutes();
  let more = null;
  for (const [index, r] of routes.entries()) {
    const card = el("article", "route" + (selected === r.id ? " selected" : ""));
    card.dataset.routeId = r.id;
    const tag = el("div", "route-label");
    const unknownHard = r.checks.find((c) => c.hard && c.status === "unknown");
    tag.append(
      el("span", "", modeNames[r.mode]),
      el(
        "span",
        "",
        r.feasibility === "satisfied"
          ? "满足已设硬条件"
          : r.feasibility === "violated"
            ? "有条件不满足"
            : unknownHard?.field === "arrive_by"
              ? "到达截止仅能估算"
              : "有硬条件待确认",
      ),
    );
    const top = el("div", "route-top");
    const duration = el("span", "duration", r.duration_s == null ? "—" : String(Math.ceil(r.duration_s / 60)));
    duration.append(el("span", "unit", " 分钟"));
    const costEstimate = drivingCostEstimate(r);
    top.append(
      duration,
      el(
        "span",
        "cost",
        r.cost_yuan === null
          ? r.mode === "driving"
            ? costEstimate
              ? `燃油车约 ¥${costEstimate.low}–${costEstimate.high}`
              : "驾车费用无法估算"
            : "费用未提供"
          : `¥${r.cost_yuan}`,
      ),
    );
    const arrival = arrivalEstimate(r);
    const arrivalText = arrival
      ? `预计${arrival.day_offset > 0 ? `次日${arrival.day_offset > 1 ? arrival.day_offset + "天后 " : " "}` : " "}${arrival.time} 到达`
      : "预计到达时间待计算";
    const combo = el("div", "line-combo");
    combo.append(icon(icons[r.mode]));
    const lines = r.segments.filter((s) => s.mode !== "walking").map((s) => s.instruction.replace(/[（(].*$/, ""));
    combo.append(
      el(
        "span",
        "",
        r.mode === "driving"
          ? "全程驾车 · " + fmt(r.distance_m == null ? null : r.distance_m / 1000, "公里")
          : r.mode === "walking"
            ? "全程步行"
            : lines.join(" → ") || "公共交通",
      ),
    );
    card.append(
      tag,
      top,
      el("p", "arrival-time", arrivalText),
      combo,
      el(
        "p",
        "route-meta",
        `${
          r.mode === "driving" && r.walk_m == null
            ? r.parking_access?.walk_m != null
              ? `停车后约步行 ${fmt(r.parking_access.walk_m, "米")}`
              : "停车后步行待重新规划估算"
            : "总步行 " + fmt(r.walk_m, "米")
        } · 换乘 ${fmt(r.transfers, "次")}`,
      ),
    );
    if (r.tradeoff) card.append(el("p", "tradeoff", r.tradeoff));
    for (const check of r.checks.filter((c) => c.status !== "satisfied"))
      card.append(
        el(
          "p",
          "check " + check.status,
          checkText(check),
        ),
      );
    const actions = el("div", "route-actions");
    const choose = el("button", "text-button", selected === r.id ? "已选中" : "在地图上查看");
    choose.type = "button";
    choose.setAttribute("aria-pressed", String(selected === r.id));
    choose.onclick = () => {
      selected = r.id;
      renderRoutes();
      drawMap(false);
      refreshIcons();
    };
    actions.append(choose);
    card.append(actions);
    const detail = el("details");
    detail.addEventListener("toggle", () => {
      summary.firstChild.textContent = detail.open ? "返回方案列表" : "查看步骤";
      if (detail.open) {
        selected = r.id;
        root.querySelectorAll(".route").forEach((n) => n.classList.toggle("selected", n.dataset.routeId === r.id));
        drawMap(false);
      }
    });
    const summary = el("summary", "", "查看步骤");
    summary.append(icon("chevron-down"));
    detail.append(summary);
    const steps = el("ol", "steps");
    for (const s of r.segments) {
      steps.append(el("li", "", s.instruction + (s.departure ? ` · ${s.departure} → ${s.arrival || "待确认"}` : "")));
    }
    if (!r.segments.length) steps.append(el("li", "", "服务未返回详细步骤"));
    detail.append(steps);
    for (const c of r.checks) {
      detail.append(
        el(
          "p",
          "check " + c.status,
          `${checkText(c)}${c.reason && c.estimated_on_time == null ? "。" + c.reason : ""}`,
        ),
      );
    }
    if (costEstimate) {
      const energyLow = costEstimate.energy_low ?? Math.max(0, costEstimate.low - (r.tolls_yuan || 0));
      const energyHigh = costEstimate.energy_high ?? Math.max(0, costEstimate.high - (r.tolls_yuan || 0));
      const tollText = r.tolls_yuan == null ? "道路通行费未返回" : `道路通行费预计 ¥${r.tolls_yuan}`;
      detail.append(
        el(
          "p",
          "",
          `默认燃油车油费约 ¥${energyLow}–${energyHigh}：按百公里6–10升、参考油价7.5元/升估算；不是实时油价或具体车型报价。${tollText}。当前可估支出合计约 ¥${costEstimate.low}–${costEstimate.high}；未计入${costEstimate.excludes}，也不是打车价格。`,
        ),
      );
    }
    if (r.parking_access?.walk_m != null)
      detail.append(
        el(
          "p",
          "",
          `${r.parking_access.parking_name}至终点约步行 ${fmt(r.parking_access.walk_m, "米")}（约 ${Math.max(1, Math.ceil(r.parking_access.walk_duration_s / 60))}分钟）；停车场是否可用及收费仍需核实。`,
        ),
      );
    const hasArrivalEstimate = r.checks.some((c) => c.field === "arrive_by");
    const normalizedLimitations = r.assumptions.map((text) => {
      if (text.includes("耗时为预计值")) return "路线耗时为地图服务的预计值。";
      if (text.includes("通行费不是打车费用")) return "油费按默认燃油车和非实时参考油价估算；停车费、车辆折旧和时间成本另行核实。";
      if (text.includes("不按所选日期与时间预测")) return "该方式未按所选日期和时间预测交通情况。";
      return text;
    });
    const limitations = [...new Set(normalizedLimitations)].filter(
      (text) => !(hasArrivalEstimate && text.includes("路线耗时为地图服务的预计值")),
    );
    if (limitations.length) {
      const limits = el("details");
      limits.append(el("summary", "micro", "数据限制"));
      for (const text of limitations) limits.append(el("p", "micro", text));
      detail.append(limits);
    }
    detail.append(el("p", "", `${r.source} · ${new Date(r.retrieved_at).toLocaleString("zh-CN")}`));
    if (state.confirmed.destination && r.data_kind === "real") {
      const p = state.confirmed.destination;
      detail.append(
        safeLink(
          "https://uri.amap.com/navigation?to=" +
            encodeURIComponent(p.location + "," + p.name) +
            "&mode=" +
            { transit: "bus", walking: "walk", driving: "car" }[r.mode],
          "打开导航",
        ),
      );
    }
    card.append(detail);
    if (index < 3) {
      root.append(card);
    } else {
      if (!more) {
        more = el("details");
        more.append(el("summary", "", `查看其余 ${routes.length - 3} 个候选`));
        root.append(more);
      }
      more.append(card);
    }
  }
  if (!routes.length && state.result_type === "clarification" && !Object.keys(state.place_candidates).length) {
    const empty = el("div", "micro", "先确定地点与时间，再比较耗时、步行和费用。");
    root.append(empty);
  }
}
function renderEvidence() {
  const root = $("evidence");
  root.hidden = false;
  root.replaceChildren();
  root.classList.remove("local-reminder");
  if (!state.evidence.length) {
    const statusText = {
      online_not_configured: "现有资料不足，当前暂时无法联网核实；关键通行条件仍需确认。",
      not_found: "没有找到可支持该陈述的资料；关键通行条件仍待确认。",
      search_failed: "联网证据查询失败；没有把搜索摘要当作通行事实。",
    }[state.evidence_status];
    if (statusText) root.append(el("p", "micro", statusText));
    else if (visibleRoutes().length)
      root.append(el("p", "micro", "暂无适用且有效的本地资料；站口与设施状态需自行核实。"));
    return;
  }
  root.classList.add("local-reminder");
  const onlineCandidate = state.evidence_status === "candidate_evidence";
  const generalOnly = state.evidence.every((e) => e.metadata.knowledge_scope === "national");
  root.append(el("h3", "", onlineCandidate ? "联网候选资料" : generalOnly ? "通用出行提醒" : "出行资料提醒"));
  if (!state.message.includes(state.evidence[0].claim)) root.append(el("p", "reminder-claim", state.evidence[0].claim));
  root.append(
    el(
      "p",
      "micro",
      onlineCandidate
        ? "网页原文候选；尚未确认地点、方向、人员与有效时间，也不代表实时开放状态。"
        : generalOnly
          ? "通用出行资料；暂无匹配的本地入口或设施资料。"
          : "相关地区与设施资料；具体入口及当日通行情况仍需核实。",
    ),
  );
  const details = el("details");
  details.append(
    el("summary", "", `${onlineCandidate ? "查看来源与待核实项" : "核验来源与适用范围"} · ${state.evidence.length}`),
  );
  for (const e of state.evidence) {
    const box = el("div", "source");
    box.append(el("strong", "", e.title), el("p", "", e.claim), safeLink(e.source, "查看原始资料"));
    const m = e.metadata || {};
    box.append(
      el(
        "p",
        "",
        `${onlineCandidate ? "获取" : "核验"}：${m.retrieved_at || m.verified_at || "未知"} · 适用：${m.facility_area || m.facility || m.city || "未标明"} · 版本：${m.version || "未知"}`,
      ),
      el(
        "p",
        "",
        `有效期：${m.valid_from || "未知"} 至 ${m.valid_until || "未注明结束时间"} · ${m.data_kind === "real" ? "真实资料" : "演示资料"}`,
      ),
      el("p", "micro", e.scope_note),
    );
    details.append(box);
  }
  root.append(details);
}
function points(polyline) {
  return (polyline || "")
    .split(";")
    .map((v) => v.split(",").map(Number))
    .filter((p) => p.length === 2 && p.every(Number.isFinite));
}
function drawMap(fit) {
  if (!map) {
    if (config.map_renderer === "static") drawStaticMap();
    return;
  }
  map.remove(overlays);
  overlays = [];
  for (const r of visibleRoutes()) {
    for (const line of r.geometry) {
      const path = points(line);
      if (path.length < 2) continue;
      const active = selected === r.id;
      const poly = new AMap.Polyline({
        path,
        strokeColor: dirty ? "#88928d" : active ? "#176b58" : "#9caea8",
        strokeWeight: active ? 6 : 4,
        strokeOpacity: active ? 0.95 : 0.55,
        zIndex: active ? 30 : 10,
      });
      poly.on("click", () => {
        selected = r.id;
        renderRoutes();
        drawMap(false);
        refreshIcons();
      });
      overlays.push(poly);
    }
  }
  for (const [key, p] of Object.entries(state?.confirmed || {})) {
    if (p) {
      const marker = new AMap.Marker({
        position: p.location.split(",").map(Number),
        title: (key === "origin" ? "起点：" : "终点：") + p.name,
      });
      overlays.push(marker);
    }
  }
  map.add(overlays);
  const sig = JSON.stringify(visibleRoutes().map((r) => r.geometry));
  if (fit && sig !== mapSignature) {
    fitAll();
    mapSignature = sig;
  }
  if (overlays.length) $("map-empty").hidden = true;
}
function fitAll() {
  if (config.map_renderer === "static") {
    staticSignature = "";
    drawStaticMap();
  }
  if (map && overlays.length) map.setFitView(overlays, false, [60, 45, 60, 45], 16);
}
async function initMap() {
  if (config.map_renderer === "static") {
    $("map-config").textContent = "确认起终点后显示真实底图与路线。";
    $("map-legend").textContent = "路线概览（轨迹已抽稀）· 静态地图不支持拖动";
    return;
  }
  if (!config.js_map_key) {
    $("map-config").textContent = "地图底图未配置：请设置浏览器用途 AMAP_JS_KEY。服务端路线 Key 单独配置。";
    return;
  }
  window._AMapSecurityConfig = { securityJsCode: config.js_security_code };
  window.onAmapReady = () => {
    try {
      map = new AMap.Map("map", { zoom: 12, center: [121.4737, 31.2304], mapStyle: "amap://styles/normal" });
      map.on("complete", () => {
        $("map-empty").hidden = true;
        drawMap(true);
      });
    } catch {
      $("map-config").textContent = "地图初始化失败，请检查浏览器 Key 和域名配置。";
    }
  };
  const script = document.createElement("script");
  script.src =
    "https://webapi.amap.com/maps?v=2.0&key=" + encodeURIComponent(config.js_map_key) + "&callback=onAmapReady";
  script.onerror = () => {
    $("map-config").textContent = "地图加载失败，请检查网络或浏览器 Key 配置。";
  };
  document.head.append(script);
  setTimeout(() => {
    if (!map) $("map-config").textContent = "地图未能加载，请检查网络及地图配置。";
  }, 12000);
}
$("trip-form").onsubmit = (e) => {
  e.preventDefault();
  send({ patch: currentPatch() });
};
$("trip-form").addEventListener("input", markDirty);
$("depart_at").addEventListener("input", updateTimeHint);
$("arrive_by").addEventListener("input", updateTimeHint);
$("message-form").onsubmit = (e) => {
  e.preventDefault();
  if ($("message").value.trim()) send({ message: $("message").value, patch: dirty ? currentPatch() : {} });
};
$("message").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("message-form").requestSubmit();
  }
});
$("new-trip").onclick = () => send({ new_trip: true });
$("fit-map").onclick = fitAll;
let dragged = false;
function sheetState(expanded) {
  document.body.classList.toggle("expanded", expanded);
  $("sheet-handle").setAttribute("aria-expanded", String(expanded));
  if (map) map.resize();
  else if (config.map_renderer === "static") drawStaticMap();
}
$("sheet-handle").onclick = () => {
  if (dragged) {
    dragged = false;
    return;
  }
  sheetState(!document.body.classList.contains("expanded"));
};
let dragStart;
$("sheet-handle").addEventListener("pointerdown", (e) => {
  dragStart = e.clientY;
  dragged = false;
  $("sheet-handle").setPointerCapture(e.pointerId);
});
$("sheet-handle").addEventListener("pointerup", (e) => {
  if (Math.abs(e.clientY - dragStart) > 20) {
    dragged = true;
    sheetState(e.clientY < dragStart);
  }
});
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", () => {
    document.body.classList.toggle("keyboard", visualViewport.height < window.innerHeight * 0.75);
    document.querySelector(".workspace").style.height = Math.max(200, visualViewport.height - 56) + "px";
    if (map) map.resize();
    else if (config.map_renderer === "static") drawStaticMap();
  });
}
(async () => {
  setBusy(true);
  $("status").textContent = "正在恢复行程…";
  refreshIcons();
  try {
    config = await (await fetch("/api/config")).json();
    if (config.mode === "demo") $("product-mode").textContent = "离线固定数据演示";
    $("language-note").textContent = config.natural_language
      ? "输入出行需求、修改条件或查询本地资料"
      : "自然语言服务未配置；可用表单、天气与本地知识";
    initMap();
    const id = new URLSearchParams(location.search).get("session") || localStorage.getItem("trip-session");
    if (id) {
      const r = await fetch("/api/sessions/" + encodeURIComponent(id));
      if (r.ok) {
        state = await r.json();
        localStorage.setItem("trip-session", state.session_id);
        history.replaceState(null, "", location.pathname);
        selected = state.routes[0]?.id;
        render();
        return;
      }
      if (r.status === 404) localStorage.removeItem("trip-session");
      else {
        restoreFailed = true;
        throw Error("Restore failed");
      }
    }
    const now = new Date();
    $("date").value = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(now);
  } catch {
    $("status").className = "status error";
    $("status").textContent = "无法连接服务，请检查后端是否启动。";
  } finally {
    setBusy(false);
  }
})();

let staticSignature = "",
  staticToken = 0;
function drawStaticMap() {
  const existing = $("map").querySelector("img");
  if (existing) existing.classList.toggle("outdated-map", dirty);
  if (dirty) {
    staticToken++;
    staticSignature = "";
    return;
  }
  if (!state?.confirmed.origin || !state?.confirmed.destination) {
    staticToken++;
    staticSignature = "";
    $("map").replaceChildren();
    $("map-empty").hidden = false;
    return;
  }
  const bounds = $("map").getBoundingClientRect();
  const width = Math.max(240, Math.min(1024, Math.round(bounds.width)));
  const height = Math.max(180, Math.min(1024, Math.round(bounds.height - 36)));
  const signature = [state.session_id, state.revision, selected, width, height].join(":");
  if (signature === staticSignature) return;
  staticSignature = signature;
  const token = ++staticToken;
  const img = new Image();
  img.alt = "高德路线概览：A为起点，B为终点，绿色为选中方案";
  img.className = "static-map-image";
  $("map-config").textContent = "正在加载真实底图…";
  const pending = $("map").querySelector("img");
  if (pending) pending.classList.add("outdated-map");
  img.onload = () => {
    if (token !== staticToken) return;
    $("map").replaceChildren(img);
    $("map-empty").hidden = true;
  };
  img.onerror = () => {
    if (token !== staticToken) return;
    $("map").replaceChildren();
    $("map-empty").hidden = false;
    $("map-config").textContent = "底图暂时不可用，路线结果仍可在左侧查看；请稍后重试。";
    staticSignature = "";
  };
  img.src =
    "/api/sessions/" +
    state.session_id +
    "/map?" +
    new URLSearchParams({ route_id: selected || "", width, height, revision: state.revision });
}
let mapResizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(mapResizeTimer);
  mapResizeTimer = setTimeout(() => {
    if (config.map_renderer === "static") drawStaticMap();
  }, 180);
});
