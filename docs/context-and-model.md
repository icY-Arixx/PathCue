# PathCue 的定位、天气与模型工具

产品名：**PathCue｜情境感知与知识增强的出行助手**。

页面说明：**结合你的位置、天气和出行要求，找到合适的路线。**

本轮沿用现有出行项目，没有合并 ContextPilot 的模拟工具、默认电量与默认天气，也没有引入第二套编排器。借鉴其按任务选择上下文的分层思路，以真实适配器实现网页定位与天气。

## 配置智谱

在项目根目录的 `.env` 中配置，凭证不提交、不返回浏览器：

```dotenv
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_MODEL=glm-4.7-flash
LLM_API_KEY=在本机填写自己的智谱Key
```

填写后重启 FastAPI。以 `/api/config` 的 `model_tools` / `natural_language` 判断是否配置；这只表示配置存在，不代表凭证已经验证有效。不自动切换收费模型。官方公布 GLM-4.7-Flash 供免费调用；实际可用性、并发与额度取决于账户和提供商，不保证永久免费。

- [智谱官方发布](https://www.zhipuai.cn/zh/news/148)
- [智谱工具调用文档](https://docs.bigmodel.cn/cn/guide/capabilities/function-calling)
- [API Key 管理](https://open.bigmodel.cn/apikey/platform)

`ToolPlanner` 每轮最多一次模型规划请求，15秒超时，输出最多3个不同工具：

| 工具 | 实际执行 |
| --- | --- |
| `plan_trip(patch)` | 更新本轮明确字段，进入已有地点确认、Amap 查询、RAG 和条件校验流程 |
| `lookup_weather(city?, date?)` | 调用真实高德天气；省略值沿用行程，独立查询不丢路线 |
| `search_knowledge(question)` | 按行程范围过滤检索，返回有原文支持的陈述，保留路线 |
| `clarify(question)` | 停止执行并请求必要澄清，不能与其他工具同时返回 |

拒绝未知工具、重复调用、超量调用、非法 JSON、额外参数及无效行程字段。模型不能确认地点、修改硬限制的类别、调用任意 URL、运行 shell 或读取设备。工具选择后不进入自主循环；最终路线数值和天气直接展示工具数据，知识陈述仍由可核对的抽取句支持。模型故障退回有限规则并明确提示；知识生成故障保留抽取式回答。

模型上下文只包含当前结构化需求和最近两轮用户消息，不包含原始 GPS 坐标、定位地址、地图原始响应或全量历史。手填地点与用户消息本身会作为自然语言理解的上下文发送给模型。

## 网页定位

用户点击“使用当前位置”才调用浏览器 Geolocation；页面提前说明坐标会发送到本服务与高德，用于起点地址及路线。公网部署需 HTTPS；拒绝授权、浏览器不支持、设备无法定位、超时或地址查询失败时，仍可手填起点。

`POST /api/location` 接收 `longitude, latitude, accuracy_m, obtained_at`，要求有限数值、合法范围及近2分钟内带时区的采集时间。浏览器 WGS84 坐标通过高德官方接口转换为 GCJ-02，再逆地理编码，返回带精度说明的候选与5分钟单次 token。`POST /api/trips` 提交 `location_token` 后仍要求确认起点，不把定位当作可通行校门。用户拒绝定位不会自动请求 IP 定位。

精确的已确认起点坐标会随行程保存在本机 SQLite；短期 token 最多128项，重启失效。单实例原型没有公网鉴权，不应直接作为多人公共定位服务部署。

- [浏览器定位](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation/getCurrentPosition)
- [高德坐标转换](https://lbs.amap.com/api/webservice/guide/api/convert)

## 真实天气

复用服务端 `AMAP_API_KEY`，按城市编码查询。当天使用实况；未来日期使用接口实际返回的对应日预报；超出范围、缺少资料或城市不唯一均明确返回状态。不根据当天实况推断明天，不承诺小时级降雨。

结果带城市、日期、实况/预报类型、来源、发布时间、获取时间及适用范围。实况超过3小时、预报超过36小时或无法核实发布时间时标为过期，降雨判断改为未知；会话恢复会重新核对时效。天气失败不抹去已查路线，不用默认“小雨”兜底。修改行程的城市、日期或地点会清除旧天气。

路线查询同任务补充天气；也可提交 `POST /api/trips` 的 `{"session_id":"已有UUID","action":"weather"}` 单独更新天气。天气与其他地图 HTTP 请求共享每任务8次、40秒上限；定位是单独最多18秒的请求。

[高德天气官方接口与预报范围](https://lbs.amap.com/api/webservice/guide/api/weatherinfo)

## 仍不包含

读取手机电池、后台定位、日历授权、创建系统提醒、操作其他 App、按天气自动改变硬条件、地铁与打车拼接、校门公告与路线的空间匹配。原 RAG 的真实/演示、地域、设施、日期、夜间时段过滤与引用规则继续生效。
