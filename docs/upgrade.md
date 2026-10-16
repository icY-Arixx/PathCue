# PathCue 升级说明

PathCue｜情境感知与知识增强的出行助手。结合你的位置、天气和出行要求，找到合适的路线。

## 启动

Python 3.10+。保留 FastAPI 和原生 HTML/CSS/JS，不新增前端构建链。首次运行需安装文档依赖以读取已有 DOCX/PDF 语料。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install ".[api,documents,dev]" httpx
if (!(Test-Path .env)) { Copy-Item .env.example .env }
$env:EMBEDDING_BACKEND='hash' # 轻量离线启动；使用既有神经模型时移除此行
.\.venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

访问 http://127.0.0.1:8000 。以上轻量启动命令使用 hash embedding；.env.example 保留原 model2vec 配置。要沿用神经向量基线，安装 `.[embeddings]` 后设 `EMBEDDING_BACKEND=model2vec`，首次需要下载模型。没有做模型优劣实验，也没有用 hash 结果宣称检索质量提升。

- `AMAP_API_KEY`：服务端 Web 服务 Key，仅后端使用。
- 只有 Web 服务 Key 时，自动使用服务端代理的真实静态底图；切换方案会更新高亮，所有候选共用同一视野。静态模式不支持拖动、缩放，显示轨迹经过抽稀的概览；失败会明确提示，不改用虚构地图。
- `AMAP_JS_KEY`、`AMAP_JS_SECURITY_CODE`：浏览器用途的 JS API 配置，需正确配置域名白名单。JS 安全码是浏览器用途配置，不能填服务端 Key。生产可改用高德官方代理方案。
- `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`：默认智谱 `glm-4.7-flash` 工具调用与知识生成服务。没有 Key 时使用有限中文规则与抽取式知识回答，必须核对表单。定位、天气与模型配置详见 [context-and-model.md](context-and-model.md)。
- `EMBEDDING_API_MODEL`、`EMBEDDING_DIMENSIONS`、`EMBEDDING_REVISION`：API embedding 的具体模型、固定维数和人工版本标识。远端模型原地换权重需主动递增版本。

不提供自动切换到虚构路线的机制。生产入口永远不注册测试地图客户端。

## 从实际代码确认的改造

| 原始代码行为 | 当前实现 |
| --- | --- |
| `/query` 直接调用 RAG | `/api/trips` 和兼容 `/query` 都进入有限 LangGraph |
| 图地图分支仅返回配置提示 | 地点确认后调用 AmapClient 查询各交通方式，随后同任务检索知识 |
| 首条驾车路线、无会话 | 保留多个候选、统一单位；SQLite 保存需求、确认位置、证据、历史与状态 |
| 浏览器直接插入答案 HTML | 动态内容使用 textContent / DOM 创建，来源链接只允许 HTTP(S) |
| sidecar 更新不改变索引指纹 | 元数据、具体模型、版本、维数均参与指纹；本地权重内容也纳入版本 |
| Milvus 丢弃元数据 | JSON metadata 存取；旧 schema 明确拒绝，不删除旧 collection |

未发现仓库或上级目录中的适用 AGENTS.md。本次没有添加多智能体，也没有引入无限规划循环。

## 状态与工作流

`trips.py` 定义 Pydantic 需求与请求契约；`assistant.py` 执行节点；`graph.py` 只定义有限图；`sessions.py` 管理单实例 SQLite。

```text
理解与局部更新 → 缺字段澄清 → 起终点候选确认
 → Amap 多路线查询 + 经过滤的知识检索 → 硬条件三态校验 → 软偏好排序 → 结构化解释
```

所有地图候选都需要确认，即使只返回一项。地点/城市变更清除旧确认。`new_trip=true` 或明确说“新行程/换个行程/换一趟”会建立新 trip_id，默认不继承任何旧条件。历史保留最多20轮；刷新页面通过 localStorage 中的 session_id 恢复服务端状态。请求级锁防止丢失更新，SQLite 原子保存；只适合单进程、单实例原型。

地图与天气每任务最多8次实际 HTTP 请求、40秒预算，单次最多8秒；真实 AmapClient 与调用预算按请求创建。模型工具选择每轮一次、15秒、最多3个不同工具，知识生成10秒、API embedding 30秒。独立定位请求最多18秒，仍需用户确认地点。图 recursion_limit=10，没有无限规划循环。会话修改按 session_id 分锁；同一会话仍串行，跨进程队列与分布式锁尚未实现。

本地模式不启用身份校验。显式设置 `PUBLIC_ACCESS_MODE=true` 时，服务端使用签名、HttpOnly、SameSite Cookie 绑定匿名访客，会话、地图和定位凭据检查归属；`session_id` 本身不能授权访问。公网前仍需加入速率限制、全站预算、保留策略和网关保护。

## API

- `GET /api/config`：仅公开前端地图配置与服务可用性，不返回服务端 Key。
- `POST /api/trips`：创建、更新、确认或重规划。
- `POST /api/location`：主动定位后的 WGS84 坐标转换与地址查询，返回单次 location_token；交给 /api/trips 后仍需确认。
- `GET /api/sessions/{session_id}`：恢复统一状态。
- `GET /api/sessions/{session_id}/map?route_id=...&width=1000&height=760`：无 JS Key 时的高德静态地图代理，服务端 Key 不出现在浏览器。单次8秒，32项内存缓存；该调用独立于路线工具预算。
- `POST /query`：保留 question / answer / citations / trace / latency_ms 等兼容键，同时返回新状态。旧 confidence、引用 score 为 null，避免误用为正确概率；旧 intent 语义升级为结果类型。
- `/feedback`、`/health` 保留。

```json
{
  "session_id": "可省略，首次由服务端创建",
  "trip_id": "已有行程可提供，拒绝对已切换行程的旧请求",
  "message": "预算从30提高到50，其他不变",
  "patch": {"budget": 50},
  "confirm": {"origin": "候选id", "destination": "候选id"},
  "new_trip": false
}
```

示例中 session_id 说明文字不是合法 UUID，调用时省略或使用响应值。结构化 patch 比自然语言优先；省略字段保留旧值，null 清除限额，0 是零上限。空地点会要求补充。默认时区 Asia/Shanghai；未给日期会明确按上海今天处理；出发时间与到达截止时间至少填写一项。

响应包含 `result_type=clarification|trip_plan|knowledge|no_solution|error`、session_id、trip_id、revision、requirements、confirmed、pending_fields、place_candidates、routes、evidence、weather、tool_status、notes、history。`action=weather` 可单独更新天气，不改变路线与需求。错误和澄清不会保留旧路线作为当前推荐；previous_routes 如存在均标 stale。UI 本地修改未提交时也立即将旧方案标为失效。

路线数值单位为秒、米、人民币元；包含 segments、geometry（GCJ-02 polyline）、source、retrieved_at、missing_fields、assumptions、checks、feasibility。未知字段为 null。checks 每项返回 satisfied / violated / unknown。先按硬条件状态分组，再按用户偏好排序；no_solution 保留带违反项的备选，不自动放宽限制。

## 地图边界

[高德地点搜索](https://lbs.amap.com/api/webservice/guide/api-advanced/newpoisearch)、[地理编码](https://lbs.amap.com/api/webservice/guide/api/georegeo)、[公交和步行规划](https://lbs.amap.com/api/webservice/guide/api/direction)、[驾车2.0](https://lbs.amap.com/api/webservice/guide/api/newroute)。公交 v3 支持出发 date/time；步行 v3、驾车 v5 不进行所选时刻预测。驾车2.0请求 cost,polyline 并保留多条 paths；保留旧 plan_driving 供现有脚本和 MCP 使用。

驾车通行费单列，费用和接驳步行均未知；不推导打车价。到达截止校验只能发现“预计也来不及”的冲突，预计来得及仍为无法确认，不保证按时到达。没有地铁加打车拼接，也不能核实实时电梯或设施开放状态。

## 知识、引用与本地区域

保留 BM25、向量、RRF、启发式重排序。召回前按 audience、data_kind、城市、valid_from/valid_until、夜间 active_after/active_before 及明确 facility 过滤，稀疏与稠密两路均先过滤；Milvus 使用 eligible chunk IDs 的服务端 filter。该实现适合小语料，超大语料需要把元数据过滤表达式直接下推。会话恢复时根据当前索引和过滤条件重新核对先前引用。

旧样例均明确为 demo；维护规范为 maintainer，普通乘客查询不会召回。默认真实模式只召回真实资料。引用只保留实际使用的具体抽取句，与 chunk 原文逐字对应；不把全部检索结果自动当来源。配置 LLM 时仍把最终知识事实约束为带精确原文的抽取句，不把模型输出的引用编号当作蕴含证据。

首个真实资料区域：上海人民广场—上海博物馆人民广场馆。2026-10-16 核查[馆方页面](https://www.shanghaimuseum.net/mu/frontend/pg/service/visit-west)，记录武胜路南门及建议地铁站口；资料写明核验时间、2026-06-30 起实施、版本、真实标记和适用设施，官方未注明结束日期。这里只提供设施层补充，不把入口公告与路线几何做空间匹配，不断言当日站口或无障碍设施可用。设施名不匹配或其他城市不会召回。资料不用于自动修改终点坐标。

知识采用三层结构：全国通用基线、重点城市/设施资料、联网候选证据与短期缓存。全国基线覆盖步行过街、非机动车过街、巡游出租车枢纽载客、铁路进站和地铁换乘；城市/设施资料覆盖武汉样例与上海博物馆。道路施工、封路、积水、限行、停车、上车点和临时开放状态不能从通用规则推导，缺少本地资料时进入联网补证。搜索摘要只用于发现网页，只有受控读取的页面原文可以显示为候选引用，且仍标明适用性未确认。

真实验收行程按用户指定采用“中国地质大学（南望山校区）→汉口站”。南望山日间通行和东区夜间规则分开做时段过滤；东区夜间车行规则不推广到整座校区，夜间知识追问也不会悄悄把白天路线改为夜间路线。

## 索引升级与恢复

索引指纹加入 sidecar 元数据、embedding 模型、版本、维数。本地 model2vec 文件内容生成版本指纹；API 远端换权重需更新 EMBEDDING_REVISION。API 返回维数不符会报错，不静默改变配置。

既有本地 vectors.json / manifest.json 会先快照至 `INDEX_DIR/backups/<UTC时间>/`，再增量重建。失败不写新 manifest；恢复时停止服务，将同一快照中的两个文件复制回 INDEX_DIR。备份不会自动清理，需自行保留足够空间。

Milvus 旧集合缺 metadata 或向量维数不符时拒绝启动，绝不 drop。迁移步骤：

1. 停服务，备份原 INDEX_DIR、记下旧 collection 配置。
2. 配置**新的** `INDEX_DIR=data/index-v2` 和 `MILVUS_COLLECTION=urban_mobility_chunks_v2`。
3. 运行 `python scripts/ingest.py`，检查文档数、元数据、检索结果；用新服务执行验证。
4. 验收后切换配置；回滚时恢复旧版本代码与旧配置，旧 collection 保持原样。

同一新版 collection 的普通增量更新不是跨本地 JSON 与 Milvus 的事务；服务中断后需从本地快照恢复并重新入库，必要时再新建 collection。不要在有其他客户端使用的 collection 中直接试迁移。

详细验证和待办见 [validation.md](validation.md)。
